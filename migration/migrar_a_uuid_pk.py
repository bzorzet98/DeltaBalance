"""
DeltaBalance — migration/migrar_a_uuid_pk.py

Migra todas las tablas de INTEGER PRIMARY KEY a TEXT PRIMARY KEY (UUID v4).
Es una operación irreversible — hace backup automático antes de cualquier cambio.

Uso (parado en la raíz del proyecto, con la app CERRADA):
    python migration/migrar_a_uuid_pk.py           # dry-run: verifica sin escribir
    python migration/migrar_a_uuid_pk.py --confirmar  # aplica con backup previo
    python migration/migrar_a_uuid_pk.py --db-path otra.db [--confirmar]

--- Cómo (decisión con el usuario: base nueva y reemplazo) ---

En vez de renombrar tabla por tabla dentro de la base (en SQLite, renombrar
una tabla reescribe las FKs de las DEMÁS para que apunten a la vieja), se
arma una base NUEVA y recién al final se reemplaza el archivo:

1. Copia la base real a una carpeta de trabajo (API de backup de sqlite3:
   consistente aunque haya -wal) y, sobre esa COPIA, corre las migraciones
   de db/schema_migrations.py con ids enteros todavía (exigir_uuid=False):
   así la base vieja queda en su estructura más reciente.
2. Crea la base nueva con el db/schema.sql actual (ids UUID) + las
   migraciones, SIN el seed (todo viene de la vieja).
3. Mapa de ids: para cada tabla cuyo `id` en la base nueva es TEXT, un UUID
   v4 por fila (uuid.uuid4()). monedas y los snapshots (saldos_mensuales,
   deudas_mensuales, compartidos_mensuales) conservan su id entero.
4. Copia fila por fila, en el orden original (rowid: así "el orden de alta"
   se conserva), con FKs OFF y en UNA transacción:
   - el id → su UUID;
   - cada FK DECLARADA en la base nueva hacia una tabla con UUID → el UUID
     de la fila referenciada (sale de PRAGMA foreign_key_list: no hay una
     lista de FKs escrita a mano que se pueda desactualizar);
   - las referencias polimórficas (POLIMORFICAS: origen_tipo + origen_id de
     deudas y gastos_compartidos) → el UUID en la tabla que dice
     origen_tipo; 'manual' y 'pago_migrado' (apuntaba a deuda_pagos, que ya
     no existe) quedan en NULL;
   - sincronizado_en → NULL (las claves en Supabase cambian: todo queda
     pendiente de subir). sync_cambios / sync_estado no se copian.
   Una columna que en la vieja es INTEGER, en la nueva es TEXT y no es ni
   id ni FK ni polimórfica frena todo antes de copiar: sería un id que el
   script no sabe traducir.
5. Verifica la base nueva: misma cantidad de filas por tabla, misma suma de
   cada columna *_minor, todos los ids con formato UUID v4, PRAGMA
   foreign_key_check vacío y PRAGMA integrity_check = ok. Si algo falla,
   no se toca nada.
6. Solo con --confirmar: checkpoint de la base real, chequeo de que nadie
   más la tiene abierta (si después de cerrarla siguen el -wal / -shm, la
   app está abierta: frena), backup (DatabaseManager.hacer_backup()),
   reemplazo del archivo, y borra la marca de bajada de la sync
   (.deltabalance_prefs.json → "sync_ultima_bajada"): la primera
   sincronización después sube todo de nuevo con las claves nuevas.

Referencias rotas en la base vieja (una FK que apunta a una fila que no
existe): si la columna acepta NULL, queda NULL y se informa; si no, el
script frena y las lista.

Dry-run: hace TODO lo anterior en una carpeta temporal y descarta el
resultado — es un ensayo completo con los datos reales. La base real no se
toca (ni siquiera se le hace checkpoint).

Ver docs/DATA_MODEL_DECISIONS.md sección 25. Este script NO lo ejecuta
Claude Code (CLAUDE.md §0.1) — lo corre el usuario a mano.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db import schema_migrations
from db.database import DatabaseManager
from db.schema_migrations import MARCA_ESCRITURA_SYNC, usa_ids_uuid

RAIZ = Path(__file__).resolve().parent.parent
DB_PATH = RAIZ / "data" / "deltabalanceBZ.db"
DB_PROHIBIDA = RAIZ / "data" / "deltabalance.db"

# No se copian: las del motor de sync (se reinician) y la interna de SQLite.
TABLAS_SIN_COPIAR = {"sqlite_sequence", "sync_cambios", "sync_estado"}

# (tabla, columna) → {origen_tipo: tabla referenciada}. Un origen_tipo que
# no está acá (manual, pago_migrado) deja origen_id en NULL.
_ORIGENES = {"transaccion": "transacciones", "compra_cuotas": "compras_cuotas", "cuota_credito": "cuotas_credito"}
POLIMORFICAS: dict[tuple[str, str], tuple[str, dict[str, str]]] = {
    ("deudas", "origen_id"): ("origen_tipo", _ORIGENES),
    ("gastos_compartidos", "origen_id"): ("origen_tipo", _ORIGENES),
}

UUID_V4 = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
PREF_ULTIMA_BAJADA = "sync_ultima_bajada"
MAX_DETALLE = 15


class MigracionError(Exception):
    """La migración no puede seguir: nada se reemplazó."""


@dataclass
class Reporte:
    filas: dict[str, int] = field(default_factory=dict)          # tabla → filas copiadas
    ids_nuevos: dict[str, int] = field(default_factory=dict)     # tabla → UUIDs generados
    salteadas: dict[str, int] = field(default_factory=dict)      # tablas viejas que ya no existen → filas
    referencias_nulas: list[str] = field(default_factory=list)   # referencias rotas o sin tabla → NULL
    problemas: list[str] = field(default_factory=list)           # verificaciones que fallaron


# =============================================================
# HELPERS
# =============================================================

def _mostrar_ruta(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(RAIZ))
    except ValueError:
        return str(path)


def _conectar(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def _copiar_base(origen: Path, destino: Path) -> None:
    """Copia consistente (API de backup: incluye lo que esté en el -wal)."""
    fuente, copia = sqlite3.connect(origen), sqlite3.connect(destino)
    try:
        fuente.backup(copia)
    finally:
        copia.close()
        fuente.close()


def _tablas(conn: sqlite3.Connection) -> list[str]:
    return [
        fila["name"] for fila in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name;",
        )
    ]


def _columnas(conn: sqlite3.Connection, tabla: str) -> dict[str, dict]:
    """nombre → {tipo, notnull, pk}, en el orden de la tabla."""
    return {
        fila["name"]: {"tipo": (fila["type"] or "").upper(), "notnull": bool(fila["notnull"]), "pk": fila["pk"]}
        for fila in conn.execute(f"PRAGMA table_info({tabla});")
    }


def _fks(conn: sqlite3.Connection, tabla: str) -> dict[str, str]:
    """columna → tabla referenciada, de las FKs declaradas."""
    return {fila["from"]: fila["table"] for fila in conn.execute(f"PRAGMA foreign_key_list({tabla});")}


def _tablas_uuid(conn: sqlite3.Connection) -> set[str]:
    """Tablas de la base nueva cuyo `id` es la PRIMARY KEY y es TEXT."""
    resultado = set()
    for tabla in _tablas(conn):
        columna_id = _columnas(conn, tabla).get("id")
        if columna_id and columna_id["pk"] and columna_id["tipo"] == "TEXT":
            resultado.add(tabla)
    return resultado


# =============================================================
# MIGRACIÓN (sobre archivos de trabajo — nunca sobre la base real)
# =============================================================

def preparar_origen(path: Path) -> None:
    """
    Pone al día una COPIA de la base vieja con las migraciones de
    db/schema_migrations.py (ids enteros todavía). MigracionError si ya
    tiene ids UUID.
    """
    conn = _conectar(path)
    try:
        conn.execute("PRAGMA foreign_keys = ON;")
        if usa_ids_uuid(conn):
            raise MigracionError("La base ya tiene ids UUID: no hay nada que migrar.")
        schema_migrations.aplicar_migraciones_columna(conn, exigir_uuid=False)
        schema_migrations.aplicar_migraciones_tabla(conn)
        conn.commit()
    finally:
        conn.close()


def crear_destino(path: Path) -> None:
    """Base nueva con el schema.sql actual + migraciones, SIN el seed (el archivo ya existe, vacío)."""
    path.touch()  # existe → DatabaseManager.inicializar() no carga seed.sql
    db = DatabaseManager(db_path=path)
    try:
        db.inicializar()
    finally:
        db.desconectar()


def _plan(origen: sqlite3.Connection, destino: sqlite3.Connection, reporte: Reporte) -> list[str]:
    """
    Qué tablas se copian (en orden alfabético: con FKs OFF el orden no
    importa) y chequeo de que toda columna que pasó de INTEGER a TEXT tiene
    traducción. MigracionError si alguna no la tiene.
    """
    tablas_origen, tablas_destino = set(_tablas(origen)), set(_tablas(destino))
    for tabla in sorted(tablas_origen - tablas_destino - TABLAS_SIN_COPIAR):
        reporte.salteadas[tabla] = origen.execute(f"SELECT COUNT(*) FROM {tabla};").fetchone()[0]
    tablas_uuid = _tablas_uuid(destino)
    sin_traduccion, perdidas = [], []
    copiar = sorted((tablas_origen & tablas_destino) - TABLAS_SIN_COPIAR)
    for tabla in copiar:
        columnas_origen, columnas_destino = _columnas(origen, tabla), _columnas(destino, tabla)
        # Una columna con datos que la base nueva ya no tiene: se perderían en silencio.
        for columna in columnas_origen:
            if columna not in columnas_destino:
                con_datos = origen.execute(f"SELECT COUNT(*) FROM {tabla} WHERE {columna} IS NOT NULL;").fetchone()[0]
                if con_datos:
                    perdidas.append(f"{tabla}.{columna} ({con_datos} fila(s) con datos)")
        fks = _fks(destino, tabla)
        for columna, info in columnas_destino.items():
            viejo = columnas_origen.get(columna)
            if viejo is None or not (viejo["tipo"] == "INTEGER" and info["tipo"] == "TEXT"):
                continue
            traducible = (
                (columna == "id" and tabla in tablas_uuid)
                or fks.get(columna) in tablas_uuid
                or (tabla, columna) in POLIMORFICAS
            )
            if not traducible:
                sin_traduccion.append(f"{tabla}.{columna}")
    if sin_traduccion:
        raise MigracionError(
            "Estas columnas pasan de INTEGER a TEXT y el script no sabe a qué tabla apuntan: "
            + ", ".join(sin_traduccion) + ". Agregalas a POLIMORFICAS o declarales la FK en schema.sql."
        )
    if perdidas:
        raise MigracionError(
            "Estas columnas de la base vieja tienen datos y no existen en la nueva (se perderían): "
            + ", ".join(perdidas) + "."
        )
    return copiar


def migrar(origen_path: Path, destino_path: Path) -> Reporte:
    """
    Copia origen_path (base vieja, ya preparada con preparar_origen()) a
    destino_path (base nueva, ya creada con crear_destino()) traduciendo
    ids, y verifica el resultado. Devuelve el Reporte; si reporte.problemas
    no está vacío, la base nueva NO se debe usar. MigracionError si no se
    puede ni empezar (columna sin traducción, referencia rota en una
    columna NOT NULL).
    """
    reporte = Reporte()
    origen, destino = _conectar(origen_path), _conectar(destino_path)
    try:
        copiar = _plan(origen, destino, reporte)
        tablas_uuid = _tablas_uuid(destino)

        # 1. Mapa de ids: un UUID v4 por fila de cada tabla con id TEXT. Claves
        # como texto: una referencia puede estar guardada como 12 o como '12'
        # (una columna TEXT creada por una migración sobre la base vieja).
        mapa: dict[str, dict[str, str]] = {}
        for tabla in copiar:
            if tabla in tablas_uuid and "id" in _columnas(origen, tabla):
                mapa[tabla] = {str(fila["id"]): str(uuid.uuid4()) for fila in origen.execute(f"SELECT id FROM {tabla};")}
                reporte.ids_nuevos[tabla] = len(mapa[tabla])

        def _traducir(tabla_ref: str, valor: Any, donde: str, puede_ser_nulo: bool) -> Optional[str]:
            nuevo = mapa.get(tabla_ref, {}).get(str(valor))
            if nuevo is not None:
                return nuevo
            if not puede_ser_nulo:
                raise MigracionError(f"Referencia rota en una columna NOT NULL: {donde} = {valor!r} no existe en {tabla_ref}.")
            reporte.referencias_nulas.append(f"{donde} = {valor!r} (no existe en {tabla_ref}) → NULL")
            return None

        # 2. Copia, en una transacción, con FKs OFF y los triggers de la sync apagados.
        destino.execute("PRAGMA foreign_keys = OFF;")
        destino.execute("BEGIN;")
        try:
            destino.execute("INSERT OR REPLACE INTO sync_estado (clave, valor) VALUES (?, '1');", (MARCA_ESCRITURA_SYNC,))
            for tabla in copiar:
                columnas_destino = _columnas(destino, tabla)
                columnas_origen = _columnas(origen, tabla)
                comunes = [c for c in columnas_destino if c in columnas_origen]
                fks = {c: t for c, t in _fks(destino, tabla).items() if t in tablas_uuid}
                sql = f"INSERT INTO {tabla} ({', '.join(comunes)}) VALUES ({', '.join('?' for _ in comunes)});"
                filas_nuevas = []
                for fila in origen.execute(f"SELECT * FROM {tabla} ORDER BY rowid;"):
                    valores = []
                    for columna in comunes:
                        valor = fila[columna]
                        nulo_ok = not columnas_destino[columna]["notnull"]
                        donde = f"{tabla}.{columna}"
                        if columna == "sincronizado_en":
                            valor = None  # la sync arranca de cero
                        elif valor is None:
                            pass
                        elif columna == "id" and tabla in mapa:
                            valor = mapa[tabla][str(valor)]
                        elif columna in fks:
                            valor = _traducir(fks[columna], valor, donde, nulo_ok)
                        elif (tabla, columna) in POLIMORFICAS:
                            columna_tipo, destinos = POLIMORFICAS[(tabla, columna)]
                            tabla_ref = destinos.get(fila[columna_tipo])
                            if tabla_ref is None:
                                reporte.referencias_nulas.append(
                                    f"{donde} = {valor!r} ({columna_tipo} '{fila[columna_tipo]}': no apunta a una tabla) → NULL"
                                )
                                valor = None
                            else:
                                valor = _traducir(tabla_ref, valor, donde, nulo_ok)
                        valores.append(valor)
                    filas_nuevas.append(tuple(valores))
                destino.executemany(sql, filas_nuevas)
                reporte.filas[tabla] = len(filas_nuevas)
            destino.execute("DELETE FROM sync_estado WHERE clave = ?;", (MARCA_ESCRITURA_SYNC,))
            destino.commit()
        except Exception:
            destino.rollback()
            raise

        # 3. Verificaciones.
        reporte.problemas = verificar(origen, destino, copiar, tablas_uuid)
    finally:
        origen.close()
        destino.close()
    return reporte


def verificar(
    origen: sqlite3.Connection, destino: sqlite3.Connection, copiadas: list[str], tablas_uuid: set[str],
) -> list[str]:
    """Lista de problemas (vacía = la base nueva está bien). Ver el punto 5 del docstring."""
    problemas: list[str] = []
    for tabla in copiadas:
        n_origen = origen.execute(f"SELECT COUNT(*) FROM {tabla};").fetchone()[0]
        n_destino = destino.execute(f"SELECT COUNT(*) FROM {tabla};").fetchone()[0]
        if n_origen != n_destino:
            problemas.append(f"{tabla}: {n_origen} filas en la base vieja, {n_destino} en la nueva")
        for columna in _columnas(destino, tabla):
            if columna.endswith("_minor") and columna in _columnas(origen, tabla):
                suma_origen = origen.execute(f"SELECT TOTAL({columna}) FROM {tabla};").fetchone()[0]
                suma_destino = destino.execute(f"SELECT TOTAL({columna}) FROM {tabla};").fetchone()[0]
                if suma_origen != suma_destino:
                    problemas.append(f"{tabla}.{columna}: suma {suma_origen} en la vieja, {suma_destino} en la nueva")
        if tabla in tablas_uuid:
            malos = [
                fila["id"] for fila in destino.execute(f"SELECT id FROM {tabla};")
                if not isinstance(fila["id"], str) or not UUID_V4.match(fila["id"])
            ]
            if malos:
                problemas.append(f"{tabla}: {len(malos)} id(s) sin formato UUID v4 (ej. {malos[0]!r})")
    rotas = destino.execute("PRAGMA foreign_key_check;").fetchall()
    for fila in rotas[:MAX_DETALLE]:
        problemas.append(f"FK rota: {fila[0]} (rowid {fila[1]}) → {fila[2]}")
    if len(rotas) > MAX_DETALLE:
        problemas.append(f"… y {len(rotas) - MAX_DETALLE} FK rota(s) más")
    integridad = destino.execute("PRAGMA integrity_check;").fetchone()[0]
    if integridad != "ok":
        problemas.append(f"integrity_check: {integridad}")
    return problemas


# =============================================================
# CLI
# =============================================================

def _ensayar(db_path: Path, carpeta: Path) -> tuple[Reporte, Path]:
    """Pasos 1-5 en `carpeta`. Devuelve el reporte y la ruta de la base nueva."""
    origen = carpeta / "vieja.db"
    destino = carpeta / "nueva.db"
    _copiar_base(db_path, origen)
    preparar_origen(origen)
    crear_destino(destino)
    return migrar(origen, destino), destino


def _imprimir(reporte: Reporte) -> None:
    print("\n[FILAS COPIADAS]")
    for tabla, cantidad in reporte.filas.items():
        uuids = reporte.ids_nuevos.get(tabla)
        detalle = f"  ({uuids} id(s) nuevos)" if uuids is not None else "  (conserva su id entero)"
        print(f"  {tabla:<26} {cantidad:>7}{detalle}")
    print(f"  {'TOTAL':<26} {sum(reporte.filas.values()):>7}")
    if reporte.salteadas:
        print("\n[TABLAS VIEJAS QUE YA NO EXISTEN — no se copian]")
        for tabla, cantidad in reporte.salteadas.items():
            print(f"  {tabla:<26} {cantidad:>7} fila(s)")
    if reporte.referencias_nulas:
        print(f"\n[REFERENCIAS QUE QUEDAN EN NULL] {len(reporte.referencias_nulas)}")
        for linea in reporte.referencias_nulas[:MAX_DETALLE]:
            print(f"  ⚠️  {linea}")
        if len(reporte.referencias_nulas) > MAX_DETALLE:
            print(f"  … y {len(reporte.referencias_nulas) - MAX_DETALLE} más")
    print("\n[VERIFICACIÓN]")
    if reporte.problemas:
        for problema in reporte.problemas:
            print(f"  ❌ {problema}")
    else:
        print("  ✅ mismas filas y mismas sumas por tabla, ids UUID v4, foreign_key_check vacío, integrity_check ok")


def _cerrar_y_comprobar_libre(db_path: Path) -> None:
    """Checkpoint de la base real; si después de cerrarla siguen el -wal/-shm, otra conexión (la app) la tiene abierta."""
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
    finally:
        conn.close()
    restos = [p for p in (Path(f"{db_path}-wal"), Path(f"{db_path}-shm")) if p.exists()]
    if restos:
        raise MigracionError(
            f"La base parece estar abierta por otro programa (siguen {', '.join(p.name for p in restos)}). Cerrá la app y volvé a correr."
        )


def _olvidar_marca_de_bajada() -> None:
    """La sincronización arranca de cero: sin "sync_ultima_bajada", la próxima sube todo con las claves nuevas."""
    from ui.utils.prefs import escribir_pref, leer_pref  # app-level, solo para este paso

    if leer_pref(PREF_ULTIMA_BAJADA) is not None:
        escribir_pref(PREF_ULTIMA_BAJADA, None)


def main() -> None:
    parser = argparse.ArgumentParser(description="Migra la base a ids UUID (TEXT PRIMARY KEY).")
    parser.add_argument("--db-path", default=None, help=f"Ruta a la DB (default: {_mostrar_ruta(DB_PATH)}).")
    parser.add_argument("--confirmar", action="store_true", help="Aplica los cambios (con backup). Sin esto, solo ensayo.")
    args = parser.parse_args()

    os.chdir(RAIZ)  # .deltabalance_prefs.json de la raíz, el mismo que lee la app
    db_path = Path(args.db_path).resolve() if args.db_path else DB_PATH
    if db_path == DB_PROHIBIDA.resolve():
        sys.exit(f"❌ Este script nunca corre contra {_mostrar_ruta(DB_PROHIBIDA)}.")
    if not db_path.exists():
        sys.exit(f"❌ No existe la base {_mostrar_ruta(db_path)}.")

    modo = "APLICANDO CAMBIOS" if args.confirmar else "DRY-RUN (ensayo completo en una copia: la base real no se toca)"
    print(f"[INICIO] Migración a ids UUID — {modo}")
    print(f"[DB] {_mostrar_ruta(db_path)}")

    if not args.confirmar:
        with tempfile.TemporaryDirectory(prefix="deltabalance_uuid_") as carpeta:
            try:
                reporte, _ = _ensayar(db_path, Path(carpeta))
            except MigracionError as err:
                sys.exit(f"\n❌ {err}")
        _imprimir(reporte)
        if reporte.problemas:
            sys.exit("\n❌ El ensayo encontró problemas: no corras --confirmar hasta resolverlos.")
        print("\nNada se escribió en la base real — si el reporte está bien, corré de nuevo con --confirmar.")
        return

    # Carpeta de trabajo AL LADO de la base: el reemplazo final es un rename en el mismo disco.
    carpeta = Path(tempfile.mkdtemp(prefix=".migracion_uuid_", dir=db_path.parent))
    try:
        try:
            _cerrar_y_comprobar_libre(db_path)
            reporte, nueva = _ensayar(db_path, carpeta)
        except MigracionError as err:
            sys.exit(f"\n❌ {err}\n   La base real no se tocó.")
        _imprimir(reporte)
        if reporte.problemas:
            sys.exit("\n❌ La verificación falló: la base real no se tocó.")

        _cerrar_y_comprobar_libre(db_path)  # de nuevo, justo antes de reemplazar
        backup = DatabaseManager(db_path=db_path).hacer_backup()
        # La base nueva, entera en su archivo principal (sin -wal) antes de moverla.
        conn = sqlite3.connect(nueva)
        try:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
            conn.execute("PRAGMA journal_mode = DELETE;")
        finally:
            conn.close()
        os.replace(nueva, db_path)
        for resto in (Path(f"{db_path}-wal"), Path(f"{db_path}-shm")):
            if resto.exists():
                resto.unlink()
        _olvidar_marca_de_bajada()
    finally:
        shutil.rmtree(carpeta, ignore_errors=True)

    print(f"\n✅ Base migrada a ids UUID. Backup de la anterior: {_mostrar_ruta(backup)}")
    print("   La próxima sincronización sube todo de nuevo (las claves en Supabase cambiaron).")


if __name__ == "__main__":
    main()
