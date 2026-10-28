"""
DeltaBalance — migration/subir_a_supabase.py

Sube todos los datos de deltabalanceBZ.db a Supabase por primera vez.
Usa service_role key para bypass de RLS.

Uso:
    python migration/subir_a_supabase.py --usuario bruno
    python migration/subir_a_supabase.py --usuario noe
Por default dry-run — con --confirmar aplica.

Antes: tener creadas en Supabase las dos tablas de abajo, y en .env
SUPABASE_URL y SUPABASE_SERVICE_KEY. Ojo: sync/supabase_schema.sql y
sync/sync_engine.py todavía describen el formato remoto anterior
(deltabalance_filas con `clave`, `borrado`, `hogar_codigo`…) — este script
ya escribe el nuevo.

Formato remoto (una fila remota por fila local, la fila entera en `datos`):
- deltabalance_filas (uuid, usuario_id, tabla, datos, created_at,
  updated_at): las tablas privadas, con el usuario_id del usuario elegido.
- deltabalance_compartidos (uuid, hogar_uuid, tabla, datos, created_at,
  updated_at): las tablas compartidas (sync_engine.TABLAS_COMPARTIDAS:
  gastos_compartidos, hogares, hogar_miembros y gasto_compartido_pagos, que
  depende de gastos_compartidos), con el id del hogar de la fila. Solo se
  suben con --usuario bruno (pedido explícito).
- `uuid` es el id de la fila (ya es un UUID, docs/DATA_MODEL_DECISIONS.md
  sección 25). Las tablas sin `id` (CLAVES_SYNC: cuentas_saldos,
  hogar_miembros) usan un UUID v5 derivado de la tabla y su clave local —
  ver uuid_remoto().
- created_at / updated_at no se mandan: los maneja Supabase.

Qué hace:
- Sube TODAS las filas de las tablas sincronizadas, sin mirar conflictos
  (upsert por uuid: correrlo de nuevo pisa en Supabase con lo local).
- Marca cada fila local como sincronizada (sincronizado_en) y, si no
  estaba, deja la marca de "ya sincronizó" de ese usuario en
  .deltabalance_prefs.json: así la primera sincronización de la app en ESTA
  computadora no la trata como una restauración (en la que lo remoto gana).
  Por eso el script se corre parado en la raíz del proyecto — igual que la
  app (lo fuerza con chdir).

Dry-run (default): inicializa una COPIA temporal de la base, cuenta qué se
subiría por tabla (y a qué tabla remota), lista las filas que no se podrían
subir y cuántas filas ya hay en Supabase (solo lectura). No escribe nada,
ni local ni remoto.
--confirmar: inicializar() (columnas, tablas de control y triggers de la
sync), checkpoint + backup (DatabaseManager.hacer_backup()), y la subida —
salvo que alguna fila no se pueda subir: entonces no se sube nada.
Nunca corre contra data/deltabalance.db (se rechaza aunque se pase por
--db-path).

Este script NO lo ejecuta Claude Code (CLAUDE.md §0.1) — lo corre el
usuario a mano.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db.database import DatabaseManager
from db.schema_migrations import CLAVES_SYNC, TABLAS_SINCRONIZADAS
from repositories.sync_repository import COLUMNAS_LOCALES, SyncRepository
from sync.supabase_client import get_service_client
# _ahora_local / _valor_json: los mismos helpers que la sync de la app (la
# fecha, en el formato de los triggers de sync_cambios).
from sync.sync_engine import EPOCA, LOTE, TABLAS_COMPARTIDAS, MarcasEnPrefs, _ahora_local, _valor_json

RAIZ = Path(__file__).resolve().parent.parent
DB_PATH = RAIZ / "data" / "deltabalanceBZ.db"
DB_PROHIBIDA = RAIZ / "data" / "deltabalance.db"

USUARIOS = {
    "bruno": "f5ca8a42-a039-4a46-998e-2b01dbb28e9c",
    "noe":   "3cb96dd5-e56b-47b6-803f-63410d9c3bdd",
}
# Solo este usuario sube las tablas compartidas (pedido explícito).
USUARIO_COMPARTIDAS = "bruno"

TABLA_PRIVADA = "deltabalance_filas"
TABLA_COMPARTIDA = "deltabalance_compartidos"
CONFLICTO_REMOTO = "uuid"
# Espacio de nombres del UUID v5 de las filas con clave primaria compuesta.
# No cambiarlo: es lo que hace que la misma fila caiga siempre en la misma
# fila remota.
NAMESPACE_CLAVE_COMPUESTA = uuid.NAMESPACE_URL
MAX_PROBLEMAS_EN_REPORTE = 20


def _mostrar_ruta(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(RAIZ))
    except ValueError:
        return str(path)


def _tablas(usuario: str) -> list[str]:
    if usuario == USUARIO_COMPARTIDAS:
        return list(TABLAS_SINCRONIZADAS)
    return [t for t in TABLAS_SINCRONIZADAS if t not in TABLAS_COMPARTIDAS]


def _destino(tabla: str) -> str:
    return TABLA_COMPARTIDA if tabla in TABLAS_COMPARTIDAS else TABLA_PRIVADA


def uuid_remoto(tabla: str, fila: dict) -> str:
    """
    La clave de la fila en Supabase. Tablas con `id`: el id, normalizado
    (ValueError si no es un UUID). Tablas con clave primaria compuesta
    (CLAVES_SYNC): UUID v5 de "deltabalance://<tabla>/<clave local>" —
    determinístico, así re-correr el script (o subir la misma fila desde
    otra computadora) pisa la misma fila remota en vez de duplicarla.
    """
    clave = SyncRepository.clave_de(tabla, fila)
    if tabla in CLAVES_SYNC:
        return str(uuid.uuid5(NAMESPACE_CLAVE_COMPUESTA, f"deltabalance://{tabla}/{clave}"))
    return str(uuid.UUID(clave))


def _hogar_uuid(tabla: str, fila: dict, hogar_de_gasto: dict[str, str]) -> Optional[str]:
    """El id del hogar de una fila compartida (None si no se encuentra)."""
    if tabla == "hogares":
        return fila["id"]
    if tabla in ("hogar_miembros", "gastos_compartidos"):
        return fila["hogar_id"]
    if tabla == "gasto_compartido_pagos":
        return hogar_de_gasto.get(fila["gasto_compartido_id"])
    return None


def _preparar(
    repo: SyncRepository, tablas: list[str], usuario_id: str,
) -> tuple[dict[str, list[tuple[str, dict]]], list[str]]:
    """
    Por tabla: [(clave local, fila remota)] listas para el upsert, y los
    problemas de las filas que no se pueden subir. No escribe nada.
    """
    hogar_de_gasto = {g["id"]: g["hogar_id"] for g in repo.todas("gastos_compartidos")}
    preparadas: dict[str, list[tuple[str, dict]]] = {}
    problemas: list[str] = []
    for tabla in tablas:
        filas: list[tuple[str, dict]] = []
        for fila in repo.todas(tabla):
            clave = repo.clave_de(tabla, fila)
            try:
                uuid_fila = uuid_remoto(tabla, fila)
            except ValueError:
                problemas.append(f"{tabla} {clave}: el id no es un UUID.")
                continue
            remota: dict[str, Any] = {
                "uuid": uuid_fila,
                "tabla": tabla,
                "datos": {c: _valor_json(v) for c, v in fila.items() if c not in COLUMNAS_LOCALES},
            }
            if tabla in TABLAS_COMPARTIDAS:
                hogar_uuid = _hogar_uuid(tabla, fila, hogar_de_gasto)
                if not hogar_uuid:
                    problemas.append(f"{tabla} {clave}: no se encontró su hogar.")
                    continue
                remota["hogar_uuid"] = hogar_uuid
            else:
                remota["usuario_id"] = usuario_id
            filas.append((clave, remota))
        preparadas[tabla] = filas
    return preparadas, problemas


def _subir(cliente: Any, repo: SyncRepository, preparadas: dict[str, list[tuple[str, dict]]], leido_en: str) -> dict[str, int]:
    """Upsert por uuid, en lotes, y marca cada lote sincronizado acá. La excepción de red o de Supabase sube tal cual."""
    subidas: dict[str, int] = {}
    for tabla, filas in preparadas.items():
        subidas[tabla] = 0
        for inicio in range(0, len(filas), LOTE):
            lote = filas[inicio:inicio + LOTE]
            cliente.table(_destino(tabla)).upsert([remota for _, remota in lote], on_conflict=CONFLICTO_REMOTO).execute()
            momento = _ahora_local()
            with repo.escritura_sync() as conn:
                for clave, _ in lote:
                    repo.marcar_sincronizada(conn, tabla, clave, leido_en, momento)
            subidas[tabla] += len(lote)
    return subidas


def _filas_remotas(usuario_id: str, hogares: list[str]) -> str:
    """Cuántas filas ya hay en Supabase: las privadas de ese usuario y las compartidas de sus hogares (solo lectura)."""
    try:
        cliente = get_service_client()
        privadas = (
            cliente.table(TABLA_PRIVADA).select("uuid", count="exact")
            .eq("usuario_id", usuario_id).limit(1).execute()
        )
        texto = f"{privadas.count if privadas.count is not None else '?'} en {TABLA_PRIVADA}"
        if hogares:
            compartidas = (
                cliente.table(TABLA_COMPARTIDA).select("uuid", count="exact")
                .in_("hogar_uuid", hogares).limit(1).execute()
            )
            texto += f", {compartidas.count if compartidas.count is not None else '?'} en {TABLA_COMPARTIDA} (sus hogares)"
    except Exception as err:
        return (
            f"no se pudo consultar ({err}) — ¿están creadas {TABLA_PRIVADA} y {TABLA_COMPARTIDA}? "
            "¿está SUPABASE_SERVICE_KEY en .env?"
        )
    return texto


def _imprimir_conteo(titulo: str, conteo: dict[str, int]) -> None:
    print(f"\n[{titulo}]")
    for tabla, cantidad in conteo.items():
        print(f"  {tabla:<24} {cantidad:>7} fila(s)  → {_destino(tabla)}")
    print(f"  {'TOTAL':<24} {sum(conteo.values()):>7} fila(s)")


def _imprimir_problemas(problemas: list[str]) -> None:
    print(f"\n[NO SE PUEDEN SUBIR] {len(problemas)} fila(s):")
    for problema in problemas[:MAX_PROBLEMAS_EN_REPORTE]:
        print(f"  ❌ {problema}")
    if len(problemas) > MAX_PROBLEMAS_EN_REPORTE:
        print(f"  … y {len(problemas) - MAX_PROBLEMAS_EN_REPORTE} más.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Sube la base local a Supabase por primera vez (service_role).")
    parser.add_argument("--usuario", required=True, choices=sorted(USUARIOS), help="Dueño de las filas en Supabase.")
    parser.add_argument("--db-path", default=None, help=f"Ruta a la DB (default: {_mostrar_ruta(DB_PATH)}).")
    parser.add_argument("--confirmar", action="store_true", help="Aplica los cambios. Sin esto, solo dry-run.")
    args = parser.parse_args()

    # La marca de "ya sincronizó" va a .deltabalance_prefs.json de la raíz (el mismo que lee la app).
    os.chdir(RAIZ)
    db_path = Path(args.db_path).resolve() if args.db_path else DB_PATH
    if db_path == DB_PROHIBIDA.resolve():
        sys.exit(f"❌ Este script nunca corre contra {_mostrar_ruta(DB_PROHIBIDA)}.")
    if not db_path.exists():
        sys.exit(f"❌ No existe la base {_mostrar_ruta(db_path)}.")

    usuario_id = USUARIOS[args.usuario]
    tablas = _tablas(args.usuario)
    modo = "APLICANDO CAMBIOS" if args.confirmar else "DRY-RUN (no se escribe nada, ni local ni en Supabase)"
    print(f"[INICIO] Subida inicial a Supabase — {modo}")
    print(f"[DB] {_mostrar_ruta(db_path)}")
    print(f"[USUARIO] {args.usuario} ({usuario_id})")
    if args.usuario != USUARIO_COMPARTIDAS:
        print(f"[AVISO] Las tablas compartidas ({', '.join(TABLAS_COMPARTIDAS)}) solo se suben con --usuario {USUARIO_COMPARTIDAS}.")

    if not args.confirmar:
        with tempfile.TemporaryDirectory(prefix="deltabalance_supabase_") as carpeta:
            copia = Path(carpeta) / "copia.db"
            origen, destino = sqlite3.connect(db_path), sqlite3.connect(copia)
            try:
                origen.backup(destino)  # copia consistente, incluido lo que esté en el -wal
            finally:
                destino.close()
                origen.close()
            db = DatabaseManager(copia)
            try:
                db.inicializar()
                repo = SyncRepository(db)
                preparadas, problemas = _preparar(repo, tablas, usuario_id)
                hogares = [h["id"] for h in repo.todas("hogares")]
            finally:
                db.desconectar()
        _imprimir_conteo("SE SUBIRÍAN", {tabla: len(filas) for tabla, filas in preparadas.items()})
        if problemas:
            _imprimir_problemas(problemas)
            print("   Con --confirmar no se subiría nada hasta corregirlas.")
        print(f"\n[EN SUPABASE] {_filas_remotas(usuario_id, hogares)}")
        print("\nNada se escribió — revisá el reporte y corré de nuevo con --confirmar para aplicar.")
        return

    cliente = get_service_client()
    db = DatabaseManager(db_path)
    try:
        db.inicializar()  # columnas sincronizado_en, sync_cambios / sync_estado y triggers
        conn = db.conn
        conn.commit()
        # El backup copia solo el archivo .db — con WAL, lo que siga en el -wal no estaría sin este checkpoint.
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
        db.hacer_backup()

        repo = SyncRepository(db)
        # Antes de leer: lo que cambie mientras se sube queda pendiente para la app.
        leido_en = _ahora_local()
        preparadas, problemas = _preparar(repo, tablas, usuario_id)
        hogares = [h["id"] for h in repo.todas("hogares")]
        if problemas:
            _imprimir_problemas(problemas)
            sys.exit("\n❌ No se subió nada: corregí esas filas y corré de nuevo.")
        try:
            subidas = _subir(cliente, repo, preparadas, leido_en)
        except Exception as err:
            sys.exit(f"\n❌ ERROR al subir: {err}\n   Lo que ya se subió quedó en Supabase y marcado acá; correrlo de nuevo es seguro (upsert).")
    finally:
        db.desconectar()

    marcas = MarcasEnPrefs()
    if marcas.leer(usuario_id) is None:
        marcas.escribir(usuario_id, EPOCA)

    _imprimir_conteo("SUBIDAS", subidas)
    print(f"\n[EN SUPABASE] {_filas_remotas(usuario_id, hogares)}")
    print("\n✅ Listo. Las filas quedaron en Supabase con el formato nuevo.")


if __name__ == "__main__":
    main()
