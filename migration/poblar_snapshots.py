"""
DeltaBalance — migration/poblar_snapshots.py

Script de una sola vez: puebla las tablas de snapshots de cierre de mes
(saldos_mensuales, deudas_mensuales, compartidos_mensuales — ver
db/schema_migrations.py MIGRACIONES_TABLA y services/snapshots_service.py)
con los datos históricos ya importados en data/deltabalanceBZ.db. Corre
SnapshotsService.recalcular_todo(): desde el primer mes con datos hasta el
mes anterior al actual. Es seguro correrlo más de una vez (el recálculo
borra y vuelve a escribir todos los snapshots).

Mismo esquema que el resto de migration/: por default es DRY-RUN — corre el
recálculo completo sobre una COPIA temporal de la base (API de backup de
sqlite3, que copia también lo que esté en el -wal), imprime el reporte y
descarta la copia: la base real no se toca. Con --confirmar: aplica el
schema (crea las tablas si faltan), hace checkpoint + backup de la DB
(DatabaseManager.hacer_backup()) y recién ahí recalcula sobre la base
real. Nunca corre contra data/deltabalance.db (se rechaza aunque se pase
por --db-path).

Al final, un chequeo por (cuenta, moneda): snapshot del mes anterior +
movimientos del mes en curso = saldo actual de la app (vw_balance_cuentas).

Uso:
    # 1) Dry-run (no escribe nada en la base real, solo reporta):
    python migration/poblar_snapshots.py

    # 2) Aplicar de verdad (hace backup antes):
    python migration/poblar_snapshots.py --confirmar

    # Opcional: contra otra DB (ej. una copia de prueba)
    python migration/poblar_snapshots.py --db-path /tmp/prueba.db --confirmar

Este script NO lo ejecuta Claude Code (CLAUDE.md §0.1) — lo corre el
usuario a mano.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
import tempfile
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db.database import DatabaseManager
from services.snapshots_service import SnapshotResult, SnapshotsService

RAIZ = Path(__file__).resolve().parent.parent
DB_PATH = RAIZ / "data" / "deltabalanceBZ.db"
DB_PROHIBIDA = RAIZ / "data" / "deltabalance.db"
TABLAS_SNAPSHOT = ("saldos_mensuales", "deudas_mensuales", "compartidos_mensuales")


def _mostrar_ruta(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(RAIZ))
    except ValueError:
        return str(path)


def _mes_anterior(hoy: date) -> tuple[int, int]:
    return (hoy.year - 1, 12) if hoy.month == 1 else (hoy.year, hoy.month - 1)


def _reporte(db: DatabaseManager, resultado: SnapshotResult) -> None:
    print(f"\n[RESULTADO] {resultado.message}")
    print(f"  Meses calculados: {resultado.meses_calculados}")
    print(f"  Tiempo: {resultado.tiempo_segundos:.2f} s")
    print("\n[FILAS POR TABLA]")
    for tabla in TABLAS_SNAPSHOT:
        fila = db.fetchone(f"SELECT COUNT(*) AS n, MIN(anio * 100 + mes) AS desde, MAX(anio * 100 + mes) AS hasta FROM {tabla};")
        rango = f"{fila['desde'] // 100}-{fila['desde'] % 100:02d} a {fila['hasta'] // 100}-{fila['hasta'] % 100:02d}" if fila["n"] else "-"
        print(f"  {tabla:<24} {fila['n']:>6} filas   ({rango})")


def _chequeo_saldos(db: DatabaseManager) -> tuple[int, int]:
    """
    Por (cuenta, moneda): snapshot del mes anterior + movimientos del mes en
    curso (mismo signo que vw_balance_cuentas) = saldo actual de la vista.
    Devuelve (ok, total).
    """
    hoy = date.today()
    anio_ant, mes_ant = _mes_anterior(hoy)
    inicio_mes = f"{hoy.year:04d}-{hoy.month:02d}-01"
    filas = db.fetchall(
        """
        SELECT v.cuenta_id, v.nombre, v.moneda, v.saldo_minor AS saldo_app,
               s.saldo_minor AS snapshot,
               (SELECT COALESCE(SUM(CASE WHEN t.tipo_movimiento = 'egreso' THEN -t.monto_minor
                                         ELSE t.monto_minor END), 0)
                FROM transacciones t
                JOIN monedas m2 ON m2.id = t.moneda_id
                WHERE t.cuenta_id = v.cuenta_id AND m2.codigo = v.moneda
                  AND t.deleted_at IS NULL AND t.fecha >= ?) AS mes_en_curso
        FROM vw_balance_cuentas v
        JOIN monedas m ON m.codigo = v.moneda
        LEFT JOIN saldos_mensuales s
               ON s.cuenta_id = v.cuenta_id AND s.moneda_id = m.id AND s.anio = ? AND s.mes = ?
        ORDER BY v.nombre, v.moneda;
        """,
        (inicio_mes, anio_ant, mes_ant),
    )
    print(f"\n[CHEQUEO] snapshot de {anio_ant:04d}-{mes_ant:02d} + mes en curso = saldo actual de la app")
    ok = 0
    for fila in filas:
        if fila["snapshot"] is None:
            print(f"  ⚠️  {fila['nombre']} {fila['moneda']}: sin snapshot (sin datos antes de este mes)")
            ok += 1  # no hay nada que comparar: el saldo anterior es el inicial
            continue
        esperado = fila["snapshot"] + fila["mes_en_curso"]
        if esperado == fila["saldo_app"]:
            ok += 1
        else:
            print(f"  ❌ {fila['nombre']} {fila['moneda']}: snapshot {fila['snapshot']} + mes {fila['mes_en_curso']}"
                  f" = {esperado}, la app dice {fila['saldo_app']}")
    print(f"  {ok}/{len(filas)} (cuenta, moneda) OK")
    return ok, len(filas)


def main() -> None:
    parser = argparse.ArgumentParser(description="Puebla los snapshots mensuales de saldos, deudas y compartidos.")
    parser.add_argument("--db-path", default=None, help=f"Ruta a la DB (default: {_mostrar_ruta(DB_PATH)}).")
    parser.add_argument("--confirmar", action="store_true", help="Aplica los cambios. Sin esto, solo dry-run.")
    args = parser.parse_args()

    db_path = Path(args.db_path) if args.db_path else DB_PATH
    if db_path.resolve() == DB_PROHIBIDA.resolve():
        sys.exit(f"❌ Este script nunca corre contra {_mostrar_ruta(DB_PROHIBIDA)}.")
    if not db_path.exists():
        sys.exit(f"❌ No existe la base {_mostrar_ruta(db_path)}.")

    modo = "APLICANDO CAMBIOS" if args.confirmar else "DRY-RUN (la base real no se toca)"
    print(f"[INICIO] Poblar snapshots mensuales — {modo}")
    print(f"[DB] {_mostrar_ruta(db_path)}")

    if not args.confirmar:
        with tempfile.TemporaryDirectory(prefix="deltabalance_snapshots_") as carpeta:
            copia = Path(carpeta) / "copia.db"
            origen = sqlite3.connect(db_path)
            destino = sqlite3.connect(copia)
            try:
                origen.backup(destino)  # copia consistente, incluido lo que esté en el -wal
            finally:
                destino.close()
                origen.close()
            print(f"[COPIA] Recalculando sobre una copia temporal: {copia}")
            db = DatabaseManager(copia)
            try:
                db.inicializar()  # crea las tablas de snapshots en la copia si faltan
                resultado = SnapshotsService(db).recalcular_todo()
                _reporte(db, resultado)
                _chequeo_saldos(db)
            finally:
                db.desconectar()
        print("\nNada se escribió en la base real — revisá el reporte y corré de nuevo con --confirmar para aplicar.")
        return

    db = DatabaseManager(db_path)
    try:
        db.inicializar()  # idempotente: schema + migraciones de columna y de tabla (crea las de snapshots)
        conn = db.conn
        # inicializar() puede dejar abierta una transacción implícita (el
        # backfill de una migración de columna recién aplicada no commitea).
        conn.commit()
        # El backup copia solo el archivo .db — con journal_mode=WAL, lo que
        # siga en el -wal no estaría en la copia sin este checkpoint.
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
        db.hacer_backup()
        resultado = SnapshotsService(db).recalcular_todo()
        _reporte(db, resultado)
        ok, total = _chequeo_saldos(db)
    finally:
        db.desconectar()
    print("\n✅ Snapshots escritos en la base real." + ("" if ok == total else " ⚠️  Revisá el chequeo de arriba."))


if __name__ == "__main__":
    main()
