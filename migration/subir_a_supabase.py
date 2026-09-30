"""
DeltaBalance — migration/subir_a_supabase.py

Sube todos los datos de deltabalanceBZ.db a Supabase por primera vez.
Usa service_role key para bypass de RLS.

Uso:
    python migration/subir_a_supabase.py --usuario bruno
    python migration/subir_a_supabase.py --usuario noe
Por default dry-run — con --confirmar aplica.

Antes: correr sync/supabase_schema.sql una vez en el SQL Editor de
Supabase (crea deltabalance_filas y deltabalance_hogar_miembros), y tener
en .env SUPABASE_URL y SUPABASE_SERVICE_KEY.

Qué hace (mismo formato que la sincronización de la app, sync/sync_engine.py
— docs/DATA_MODEL_DECISIONS.md sección 24):
- Sube TODAS las filas de las tablas sincronizadas con el usuario_id del
  usuario elegido, sin mirar conflictos (upsert: correrlo de nuevo pisa en
  Supabase con lo local). Las tablas compartidas (gastos_compartidos,
  gasto_compartido_pagos, hogares, hogar_miembros) solo se suben con
  --usuario bruno (pedido explícito).
- Marca cada fila local como sincronizada (sincronizado_en) y deja la marca
  de "ya sincronizó" de ese usuario en .deltabalance_prefs.json: así la
  primera sincronización de la app en ESTA computadora no la trata como una
  restauración (en la que lo remoto gana). Por eso el script se corre
  parado en la raíz del proyecto — igual que la app (lo fuerza con chdir).

Dry-run (default): inicializa una COPIA temporal de la base, cuenta qué se
subiría por tabla y cuántas filas de ese usuario ya hay en Supabase (solo
lectura). No escribe nada, ni local ni remoto.
--confirmar: inicializar() (columnas, tablas de control y triggers de la
sync), checkpoint + backup (DatabaseManager.hacer_backup()), subida.
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
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db.database import DatabaseManager
from db.schema_migrations import TABLAS_SINCRONIZADAS
from repositories.sync_repository import SyncRepository
from sync.supabase_client import get_service_client
from sync.sync_engine import TABLA_REMOTA, TABLAS_COMPARTIDAS, SyncEngine

RAIZ = Path(__file__).resolve().parent.parent
DB_PATH = RAIZ / "data" / "deltabalanceBZ.db"
DB_PROHIBIDA = RAIZ / "data" / "deltabalance.db"

USUARIOS = {
    "bruno": "f5ca8a42-a039-4a46-998e-2b01dbb28e9c",
    "noe":   "3cb96dd5-e56b-47b6-803f-63410d9c3bdd",
}
# Solo este usuario sube las tablas compartidas (pedido explícito).
USUARIO_COMPARTIDAS = "bruno"


def _mostrar_ruta(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(RAIZ))
    except ValueError:
        return str(path)


def _tablas(usuario: str) -> list[str]:
    if usuario == USUARIO_COMPARTIDAS:
        return list(TABLAS_SINCRONIZADAS)
    return [t for t in TABLAS_SINCRONIZADAS if t not in TABLAS_COMPARTIDAS]


def _filas_remotas(usuario_id: str) -> str:
    """Cuántas filas de ese usuario ya hay en Supabase (texto para el reporte; solo lectura)."""
    try:
        respuesta = (
            get_service_client().table(TABLA_REMOTA).select("clave", count="exact")
            .eq("usuario_id", usuario_id).limit(1).execute()
        )
    except Exception as err:
        return f"no se pudo consultar ({err}) — ¿corriste sync/supabase_schema.sql? ¿está SUPABASE_SERVICE_KEY en .env?"
    return str(respuesta.count if respuesta.count is not None else "?")


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
                conteo = {tabla: len(repo.todas(tabla)) for tabla in tablas}
            finally:
                db.desconectar()
        print("\n[SE SUBIRÍAN]")
        for tabla, cantidad in conteo.items():
            print(f"  {tabla:<24} {cantidad:>7} fila(s)")
        print(f"  {'TOTAL':<24} {sum(conteo.values()):>7} fila(s)")
        print(f"\n[EN SUPABASE] filas de {args.usuario}: {_filas_remotas(usuario_id)}")
        print("\nNada se escribió — revisá el reporte y corré de nuevo con --confirmar para aplicar.")
        return

    db = DatabaseManager(db_path)
    try:
        db.inicializar()  # columnas sincronizado_en, sync_cambios / sync_estado y triggers
        conn = db.conn
        conn.commit()
        # El backup copia solo el archivo .db — con WAL, lo que siga en el -wal no estaría sin este checkpoint.
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
        db.hacer_backup()
    finally:
        db.desconectar()  # la subida abre su propia conexión

    motor = SyncEngine(db, None, cliente=get_service_client(), usuario_id=usuario_id)
    try:
        subidas = motor.subir_todo(tablas)
    except Exception as err:
        sys.exit(f"\n❌ ERROR al subir: {err}\n   Lo que ya se subió quedó en Supabase y marcado acá; correrlo de nuevo es seguro (upsert).")

    print("\n[SUBIDAS]")
    for tabla, cantidad in subidas.items():
        print(f"  {tabla:<24} {cantidad:>7} fila(s)")
    print(f"  {'TOTAL':<24} {sum(subidas.values()):>7} fila(s)")
    print(f"\n[EN SUPABASE] filas de {args.usuario}: {_filas_remotas(usuario_id)}")
    print("\n✅ Listo. La app ya puede iniciar sesión con ese usuario y sincronizar.")


if __name__ == "__main__":
    main()
