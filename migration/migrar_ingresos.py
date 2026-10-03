"""
DeltaBalance — migration/migrar_ingresos.py

Importa la TABLA INGRESOS del Excel viejo (exportada a CSV) a la tabla
`ingresos_proyectados` en su estructura final (db/schema_migrations.py,
reestructurar_presupuestos_ingresos(): estimado + real + recurrente).

CSV esperado (por POSICIÓN de columna — la 4ª no tiene nombre):
    CONCEPTO, CATEGORIA, PRECIO, [columna negativa], MONEDA, FECHA
Default: migration/sources/REGISTRO PRINCIPAL - TABLA INGRESOS (1).csv (la
carpeta sources/ está en .gitignore); otra ruta con --csv. Separador (, ; o
tab) y encoding (UTF-8 o Latin-1) se detectan solos.

Reglas (pedido explícito):
- PRECIO positivo → monto_estimado_minor. Un PRECIO negativo no cuenta
  como estimado (se informa).
- Columna negativa (valor absoluto) → monto_real_minor.
- Solo real, sin estimado → monto_estimado_minor = monto_real_minor.
- FECHA → mes y anio (el día no se guarda).
- MONEDA: MAPA_MONEDA (ARS, USD); vacía → ARS (se informa cuántas); otra
  moneda → la fila se saltea y se informa.
- Se saltean las filas sin CONCEPTO o sin ningún monto, y las de título /
  encabezado (CONCEPTO 'CONCEPTO' o 'INGRESOS').
- es_recurrente: 1 si CATEGORIA = 'INGRESO FIJO', 0 si 'INGRESO VARIABLE'
  (otra categoría → 0, y se informa).
- Concepto: sin espacios de más y en MAYÚSCULAS (la misma regla que
  IngresosService).
- Montos: minor units con los decimales de la moneda; números en formato
  argentino ("1.046.612,39", "616221,04") — _numero() de migrar_deudas.py.
- Notas: "MIGRADO DESDE EXCEL — TABLA INGRESOS, FILA N" (N = línea del
  CSV; la 1 es el título "INGRESOS").
- Idempotencia: una fila se saltea si ya hay un ingreso con el mismo
  concepto + mes + año + moneda — en la base o antes en el mismo CSV —, así
  que se puede correr de nuevo sin duplicar.

Se inserta con SQL directo (mismo criterio que migrar_deudas.py), no con
IngresosService: sus repositorios comitean fila por fila y la importación
tiene que ser UNA transacción (todo o nada).

Mismo esquema que el resto de migration/: por default es DRY-RUN — importa
sobre una COPIA temporal de la base (API de backup de sqlite3), imprime el
reporte y descarta la copia: la base real no se toca. Con --confirmar:
inicializar() (si ingresos_proyectados todavía tiene la estructura vieja,
la convierte, con su propio backup automático), checkpoint + backup
(DatabaseManager.hacer_backup()) e importación en una sola transacción.
Nunca corre contra data/deltabalance.db (se rechaza aunque se pase por
--db-path).

migration/migrar_egresos.py reusa de acá la lectura del CSV, la moneda, el
período y el armado dry-run / --confirmar (_ejecutar()) — mismo criterio
que migrar_deudas_noe.py con migrar_deudas.py.

Uso:
    # 1) Dry-run (no escribe nada en la base real, solo reporta):
    python migration/migrar_ingresos.py

    # 2) Aplicar de verdad (hace backup antes):
    python migration/migrar_ingresos.py --confirmar

    # Opcional: otro CSV u otra DB
    python migration/migrar_ingresos.py --csv ~/Descargas/ingresos.csv --db-path /tmp/prueba.db --confirmar

Este script NO lo ejecuta Claude Code (CLAUDE.md §0.1) — lo corre el
usuario a mano.
"""

from __future__ import annotations

import argparse
import csv
import io
import sqlite3
import sys
import tempfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Callable, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db.database import DatabaseManager
from migration.migrar_deudas import _clave, _fecha, _numero

RAIZ = Path(__file__).resolve().parent.parent
DB_PATH = RAIZ / "data" / "deltabalanceBZ.db"
DB_PROHIBIDA = RAIZ / "data" / "deltabalance.db"
CSV_DEFAULT = RAIZ / "migration" / "sources" / "REGISTRO PRINCIPAL - TABLA INGRESOS (1).csv"

# Columnas por posición (ver docstring) — las mismas en la TABLA EGRESOS.
COL_CONCEPTO, COL_CATEGORIA, COL_PRECIO, COL_NEGATIVA, COL_MONEDA, COL_FECHA = range(6)
CANTIDAD_COLUMNAS = 6
ENCODINGS = ("utf-8-sig", "latin-1")

MAPA_MONEDA = {"ARS": "ARS", "USD": "USD"}
MONEDA_DEFAULT = "ARS"

CONCEPTOS_ENCABEZADO = {"CONCEPTO", "INGRESOS"}
RECURRENTE_POR_CATEGORIA = {"INGRESO FIJO": 1, "INGRESO VARIABLE": 0}
NOTA = "MIGRADO DESDE EXCEL — TABLA INGRESOS, FILA {n}"


# ============================================================
# HELPERS (también los usa migrar_egresos.py)
# ============================================================

def _mostrar_ruta(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(RAIZ))
    except ValueError:
        return str(path)


def _concepto(texto: str) -> str:
    """Sin espacios de más y en MAYÚSCULAS (la misma regla que IngresosService / PresupuestosService)."""
    return " ".join((texto or "").split()).upper()


def _leer_csv(ruta: Path) -> list[tuple[int, list[str]]]:
    """(número de línea del CSV — la primera es la 1 —, celdas completadas a CANTIDAD_COLUMNAS) de cada fila."""
    ultimo_error: Optional[Exception] = None
    for encoding in ENCODINGS:
        try:
            texto = ruta.read_text(encoding=encoding)
            break
        except UnicodeDecodeError as err:
            ultimo_error = err
    else:
        raise SystemExit(f"❌ No se pudo leer {ruta} (encoding): {ultimo_error}")
    try:
        dialecto = csv.Sniffer().sniff(texto[:4096], delimiters=",;\t")
    except csv.Error:
        dialecto = csv.excel  # coma
    # StringIO (no splitlines()): un concepto entre comillas con salto de línea sigue siendo UN registro.
    return [
        (numero_linea, celdas + [""] * (CANTIDAD_COLUMNAS - len(celdas)))
        for numero_linea, celdas in enumerate(csv.reader(io.StringIO(texto, newline=""), dialecto), start=1)
    ]


def _codigo_moneda(celda: str, avisos: Counter) -> tuple[Optional[str], str]:
    """(código de la app según MAPA_MONEDA — None si no está —, texto del Excel). Vacía → MONEDA_DEFAULT."""
    codigo_excel = _clave(celda)
    if not codigo_excel:
        avisos[f"MONEDA vacía → {MONEDA_DEFAULT}"] += 1
        return MONEDA_DEFAULT, MONEDA_DEFAULT
    return MAPA_MONEDA.get(codigo_excel), codigo_excel


def _mes_anio(celda: str) -> Optional[tuple[int, int]]:
    """(mes, anio) de la FECHA (formatos de _fecha() de migrar_deudas.py), o None si no es una fecha."""
    fecha = _fecha(celda)
    return (int(fecha[5:7]), int(fecha[:4])) if fecha else None


def _monedas(conn: sqlite3.Connection) -> dict[str, sqlite3.Row]:
    return {fila["codigo"]: fila for fila in conn.execute("SELECT id, codigo, decimales FROM monedas;")}


def _ejecutar(
    titulo: str,
    csv_path: Path,
    db_path: Path,
    confirmar: bool,
    importar: Callable[[sqlite3.Connection, list[tuple[int, list[str]]]], dict],
    reporte: Callable[[dict, int], None],
    mensaje_final: str,
) -> None:
    """
    Dry-run sobre una copia temporal, o --confirmar sobre la base con backup
    previo y todo en una transacción (ver docstring del módulo).
    importar(conn, filas) inserta sin commit y devuelve el reporte.
    """
    if db_path.resolve() == DB_PROHIBIDA.resolve():
        sys.exit(f"❌ Este script nunca corre contra {_mostrar_ruta(DB_PROHIBIDA)}.")
    if not csv_path.exists():
        sys.exit(f"❌ No se encontró el CSV en {_mostrar_ruta(csv_path)} (pasá otra ruta con --csv).")
    if not db_path.exists():
        sys.exit(f"❌ No existe la base {_mostrar_ruta(db_path)}.")

    modo = "APLICANDO CAMBIOS" if confirmar else "DRY-RUN (la base real no se toca)"
    print(f"[INICIO] {titulo} — {modo}")
    print(f"[CSV] {_mostrar_ruta(csv_path)}")
    print(f"[DB] {_mostrar_ruta(db_path)}")
    filas = _leer_csv(csv_path)

    if not confirmar:
        with tempfile.TemporaryDirectory(prefix="deltabalance_migracion_") as carpeta:
            copia = Path(carpeta) / "copia.db"
            origen, destino = sqlite3.connect(db_path), sqlite3.connect(copia)
            try:
                origen.backup(destino)  # copia consistente, incluido lo que esté en el -wal
            finally:
                destino.close()
                origen.close()
            db = DatabaseManager(copia)
            try:
                db.inicializar()  # en la copia: convierte las tablas a la estructura final si hace falta
                conn = db.conn
                conn.commit()
                conn.execute("BEGIN;")
                resultado = importar(conn, filas)
                conn.rollback()
            finally:
                db.desconectar()
        reporte(resultado, len(filas))
        print("\nNada se escribió en la base real — revisá el reporte y corré de nuevo con --confirmar para aplicar.")
        return

    db = DatabaseManager(db_path)
    try:
        db.inicializar()  # convierte las tablas a la estructura final si todavía no la tienen (con su propio backup)
        conn = db.conn
        # inicializar() puede dejar abierta una transacción implícita (backfill de una migración de columna).
        conn.commit()
        # El backup copia solo el archivo .db — con WAL, lo que siga en el -wal no estaría sin este checkpoint.
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
        db.hacer_backup()
        conn.execute("BEGIN;")
        try:
            resultado = importar(conn, filas)
            conn.commit()
        except Exception:
            conn.rollback()
            print("\n❌ ERROR — rollback completo, no se importó nada.")
            raise
    finally:
        db.desconectar()
    reporte(resultado, len(filas))
    print(f"\n✅ {mensaje_final}")


# ============================================================
# IMPORTACIÓN
# ============================================================

def _importar(conn: sqlite3.Connection, filas: list[tuple[int, list[str]]]) -> dict:
    """Inserta en `ingresos_proyectados` (sin commit: decide el caller). Devuelve el reporte."""
    monedas = _monedas(conn)
    existentes = {
        (fila["concepto"], fila["mes"], fila["anio"], fila["moneda_id"])
        for fila in conn.execute("SELECT concepto, mes, anio, moneda_id FROM ingresos_proyectados;")
    }
    salteadas: Counter = Counter()
    avisos: Counter = Counter()
    errores: list[str] = []
    insertadas: Counter = Counter()
    totales: dict[str, list[int]] = defaultdict(lambda: [0, 0])  # código → [estimado, real]

    for numero_linea, celdas in filas:
        concepto = _concepto(celdas[COL_CONCEPTO])
        if not concepto:
            salteadas["sin CONCEPTO"] += 1
            continue
        if _clave(concepto) in CONCEPTOS_ENCABEZADO:
            salteadas["título / encabezado"] += 1
            continue

        precio = _numero(celdas[COL_PRECIO])
        negativa = _numero(celdas[COL_NEGATIVA])
        if precio is not None and precio < 0:
            avisos["PRECIO negativo → no cuenta como estimado"] += 1
            precio = None
        if not precio and not negativa:
            salteadas["sin monto"] += 1
            continue

        codigo, codigo_excel = _codigo_moneda(celdas[COL_MONEDA], avisos)
        if codigo is None or codigo not in monedas:
            salteadas[f"moneda desconocida ({codigo_excel})"] += 1
            continue
        periodo = _mes_anio(celdas[COL_FECHA])
        if periodo is None:
            errores.append(f"línea {numero_linea}: fecha inválida {celdas[COL_FECHA]!r} — salteada")
            continue
        mes, anio = periodo

        moneda = monedas[codigo]
        escala = 10 ** moneda["decimales"]
        estimado = round((precio or 0) * escala)
        real = round(abs(negativa or 0) * escala)
        if not estimado:
            avisos["solo real → estimado = real"] += 1
            estimado = real
        if not estimado and not real:
            errores.append(f"línea {numero_linea}: los montos redondean a 0 — salteada")
            continue

        clave = (concepto, mes, anio, moneda["id"])
        if clave in existentes:
            salteadas["ya existe (mismo concepto, mes, año y moneda)"] += 1
            continue

        categoria = _clave(celdas[COL_CATEGORIA])
        if categoria not in RECURRENTE_POR_CATEGORIA:
            avisos[f"CATEGORIA desconocida ({categoria or 'vacía'}) → no recurrente"] += 1
        es_recurrente = RECURRENTE_POR_CATEGORIA.get(categoria, 0)

        conn.execute(
            """
            INSERT INTO ingresos_proyectados
                (concepto, monto_estimado_minor, monto_real_minor, moneda_id, mes, anio, es_recurrente, notas)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?);
            """,
            (concepto, estimado, real, moneda["id"], mes, anio, es_recurrente, NOTA.format(n=numero_linea)),
        )
        existentes.add(clave)
        insertadas["recurrentes" if es_recurrente else "no recurrentes"] += 1
        totales[codigo][0] += estimado
        totales[codigo][1] += real

    return {"insertadas": insertadas, "salteadas": salteadas, "avisos": avisos, "errores": errores, "totales": totales}


def _reporte(reporte: dict, filas_csv: int) -> None:
    insertadas = reporte["insertadas"]
    print(f"\n[CSV] {filas_csv} línea(s)")
    print(f"[IMPORTADOS] {sum(insertadas.values())} ingreso(s): "
          f"{insertadas['recurrentes']} recurrentes (INGRESO FIJO), {insertadas['no recurrentes']} no recurrentes")
    for motivo, cantidad in sorted(reporte["salteadas"].items()):
        print(f"  ⏭️  salteadas — {motivo}: {cantidad}")
    for aviso, cantidad in sorted(reporte["avisos"].items()):
        print(f"  ⚠️  {aviso}: {cantidad}")
    for error in reporte["errores"]:
        print(f"  ❌ {error}")
    if reporte["totales"]:
        print("\n[TOTAL IMPORTADO POR MONEDA]")
        for codigo, (estimado, real) in sorted(reporte["totales"].items()):
            print(f"  {codigo}: estimado {estimado / 100:>16,.2f}   real {real / 100:>16,.2f}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Importa la TABLA INGRESOS del Excel (CSV) a ingresos_proyectados.")
    parser.add_argument("--csv", default=None, help=f"Ruta al CSV (default: {_mostrar_ruta(CSV_DEFAULT)}).")
    parser.add_argument("--db-path", default=None, help=f"Ruta a la DB (default: {_mostrar_ruta(DB_PATH)}).")
    parser.add_argument("--confirmar", action="store_true", help="Aplica los cambios. Sin esto, solo dry-run.")
    args = parser.parse_args()

    _ejecutar(
        "Migración de la TABLA INGRESOS",
        csv_path=Path(args.csv).expanduser() if args.csv else CSV_DEFAULT,
        db_path=Path(args.db_path) if args.db_path else DB_PATH,
        confirmar=args.confirmar,
        importar=_importar,
        reporte=_reporte,
        mensaje_final="Ingresos importados.",
    )


if __name__ == "__main__":
    main()
