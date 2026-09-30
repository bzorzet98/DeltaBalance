"""
DeltaBalance — migration/migrar_deudas.py

Importa la TABLA DEUDAS del Excel viejo (exportada a CSV) a la tabla `deudas`
en su estructura final (db/schema.sql, docs/DATA_MODEL_DECISIONS.md sección
22): un libro de movimientos en dos tabs — 'me_deben' / 'debo' — con
monto_minor CON SIGNO (positivo = la deuda crece, negativo = un pago).

CSV esperado (por POSICIÓN de columna — el nombre de la 5ª no importa):
    CONCEPTO, PERSONA, CATEGORIA, PRECIO, [columna negativa], MONEDA, FECHA
Default: migration/sources/REGISTRO PRINCIPAL - TABLA DEUDAS.csv (la carpeta
sources/ está en .gitignore); otra ruta con --csv. Separador (, ; o tab) y
encoding (UTF-8 o Latin-1) se detectan solos; la primera fila se saltea si
es el encabezado (PERSONA en la 2ª columna).

Reglas (pedido explícito). Todo va a tab='me_deben' — la TABLA DEUDAS del
Excel es de gente que te debe; lo que debés vos se carga a mano en DEBO:
- PRECIO (col[3]) positivo → +PRECIO (la deuda crece).
- PRECIO negativo → −|PRECIO| (un pago que te hicieron).
- Columna negativa (col[4]) con valor → −|valor| (un pago que te hicieron).
- CATEGORIA "COBRO DEUDA" → cada valor, en negativo (es un cobro, aunque el
  PRECIO esté en positivo).
- Las dos columnas con valor → dos filas.
- Se saltean las filas sin PERSONA y sin ningún monto.
- Moneda: MAPA_MONEDA (ARS, USD). Vacía → ARS (se informa cuántas); otra
  moneda → la fila se saltea y se informa.
- Persona: sin espacios de más y en mayúsculas (utils/personas.py, la misma
  regla que DebtsService).
- Monto: minor units con los decimales de la moneda (ARS/USD: × 100).
  Números en formato argentino: punto = miles, coma = decimal ("1.234,56",
  "$ 500", "(500)"). Con coma, la coma es siempre el decimal ("1,5" → 1.5).
  Sin coma, un punto seguido de exactamente 3 dígitos es de miles
  ("113.400" → 113400); con otra cantidad, es decimal ("113.40").
- Fecha: AAAA-MM-DD, DD/MM/AAAA, DD/MM/AA o DD-MM-AAAA (con o sin hora).
- Notas: "MIGRADO DESDE EXCEL — TABLA DEUDAS, FILA N" (N = línea del CSV,
  la 1 es el encabezado). También sirve para no importar dos veces: una
  fila del CSV cuyas notas ya están en `deudas` se saltea (se puede correr
  de nuevo sin duplicar).

--reemplazar: antes de importar BORRA todas las filas importadas antes
desde este Excel (notas "MIGRADO DESDE EXCEL — TABLA DEUDAS…") y las vuelve
a importar todas con las reglas de arriba. Sirve si ya se había importado
con las reglas viejas (por ejemplo, las filas de COBRO DEUDA con PRECIO
positivo, que entraban como deuda y no como pago). Lo que se haya editado a
mano en esas filas desde la app se pierde. Sin --reemplazar, las ya
importadas se saltean.

Se inserta con SQL directo (mismo criterio que migrar_transacciones.py), no
con DebtsService: su chequeo de doble-click rechazaría dos movimientos
idénticos legítimos del Excel cargados uno tras otro.

Mismo esquema que el resto de migration/: por default es DRY-RUN — importa
sobre una COPIA temporal de la base (API de backup de sqlite3), imprime el
reporte y descarta la copia: la base real no se toca. Con --confirmar:
inicializar() (si la base todavía tiene una estructura vieja de deudas, la
convierte, con su propio backup automático), checkpoint + backup
(DatabaseManager.hacer_backup()) e importación en una sola transacción.
Nunca corre contra data/deltabalance.db (se rechaza aunque se pase por
--db-path).

Uso:
    # 1) Dry-run (no escribe nada en la base real, solo reporta):
    python migration/migrar_deudas.py
    python migration/migrar_deudas.py --reemplazar

    # 2) Aplicar de verdad (hace backup antes):
    python migration/migrar_deudas.py --confirmar
    python migration/migrar_deudas.py --reemplazar --confirmar

    # Opcional: otro CSV u otra DB
    python migration/migrar_deudas.py --csv ~/Descargas/deudas.csv --db-path /tmp/prueba.db --confirmar

Después de aplicar, conviene recalcular los snapshots (botón ↻ de Deudas o
python migration/poblar_snapshots.py --confirmar) para que el SALDO
ANTERIOR incluya lo importado.

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
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db.database import DatabaseManager
from db.schema_migrations import PREFIJO_NOTA_EXCEL
from utils.personas import normalizar_persona

RAIZ = Path(__file__).resolve().parent.parent
DB_PATH = RAIZ / "data" / "deltabalanceBZ.db"
DB_PROHIBIDA = RAIZ / "data" / "deltabalance.db"
CSV_DEFAULT = RAIZ / "migration" / "sources" / "REGISTRO PRINCIPAL - TABLA DEUDAS.csv"

# Columnas por posición (ver docstring).
COL_CONCEPTO, COL_PERSONA, COL_CATEGORIA, COL_PRECIO, COL_NEGATIVA, COL_MONEDA, COL_FECHA = range(7)
CANTIDAD_COLUMNAS = 7

TAB = "me_deben"
MAPA_MONEDA = {"ARS": "ARS", "USD": "USD"}
MONEDA_SI_VACIA = "ARS"
CATEGORIA_COBRO = "COBRO DEUDA"
# El prefijo viene de db/schema_migrations.py: la reestructuración de deudas
# lo usa para reconocer las filas que importó una versión anterior de este script.
NOTA = PREFIJO_NOTA_EXCEL + ", FILA {n}"
FORMATOS_FECHA = ("%Y-%m-%d", "%d/%m/%Y", "%d/%m/%y", "%d-%m-%Y", "%Y/%m/%d")
ENCODINGS = ("utf-8-sig", "latin-1")


def _mostrar_ruta(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(RAIZ))
    except ValueError:
        return str(path)


def _clave(texto: str) -> str:
    """Mayúsculas, sin tildes ni espacios de más — para comparar categorías y monedas."""
    sin_tildes = "".join(c for c in unicodedata.normalize("NFD", texto or "") if unicodedata.category(c) != "Mn")
    return " ".join(sin_tildes.upper().split())


def _numero(texto: str) -> Optional[float]:
    """Monto del CSV → float (formato argentino, ver docstring), o None si la celda está vacía / no es un número."""
    t = (texto or "").strip()
    for ruido in ("U$S", "USD", "ARS", "$", "\xa0", " "):
        t = t.replace(ruido, "")
    if t in ("", "-", "—"):
        return None
    negativo = False
    if t.startswith("(") and t.endswith(")"):
        negativo, t = True, t[1:-1]
    if t.startswith("-"):
        negativo, t = not negativo, t[1:]

    # Formato argentino: punto = miles, coma = decimal
    if "," in t:
        # La coma es siempre el decimal en formato argentino
        # Los puntos son separadores de miles — se eliminan
        t = t.replace(".", "").replace(",", ".")
    elif "." in t:
        partes = t.split(".")
        if len(partes[-1]) == 3 and len(partes) > 1:
            # Punto como separador de miles: 113.400 → 113400
            t = t.replace(".", "")
        # else: punto como decimal: 113.40 → no tocar

    try:
        valor = float(t)
    except ValueError:
        return None
    return -valor if negativo else valor


def _fecha(texto: str) -> Optional[str]:
    t = (texto or "").strip().split(" ")[0].split("T")[0]
    for formato in FORMATOS_FECHA:
        try:
            return datetime.strptime(t, formato).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def _leer_csv(ruta: Path) -> list[tuple[int, list[str]]]:
    """(número de fila — la del Excel, el encabezado es la 1 —, celdas) de cada fila de datos."""
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
    filas = []
    # StringIO (no splitlines()): un concepto entre comillas con salto de línea sigue siendo UN registro.
    for numero_fila, celdas in enumerate(csv.reader(io.StringIO(texto, newline=""), dialecto), start=1):
        if numero_fila == 1 and len(celdas) > COL_PERSONA and _clave(celdas[COL_PERSONA]) == "PERSONA":
            continue  # encabezado
        filas.append((numero_fila, celdas + [""] * (CANTIDAD_COLUMNAS - len(celdas))))
    return filas


def _movimientos(celdas: list[str], avisos: Counter) -> list[float]:
    """Los montos CON SIGNO de una fila del CSV (ver reglas en el docstring): 0, 1 o 2."""
    es_cobro = _clave(celdas[COL_CATEGORIA]) == CATEGORIA_COBRO
    if es_cobro:
        avisos[f"CATEGORIA {CATEGORIA_COBRO} → pago recibido (negativo)"] += 1
    movimientos: list[float] = []
    precio = _numero(celdas[COL_PRECIO])
    if precio:
        if precio < 0 and not es_cobro:
            avisos["PRECIO negativo → pago recibido (negativo)"] += 1
        movimientos.append(-abs(precio) if (es_cobro or precio < 0) else abs(precio))
    negativa = _numero(celdas[COL_NEGATIVA])
    if negativa:
        movimientos.append(-abs(negativa))
    if len(movimientos) == 2:
        avisos["con las dos columnas → dos filas"] += 1
    return movimientos


def _importar(conn: sqlite3.Connection, filas: list[tuple[int, list[str]]], reemplazar: bool) -> dict:
    """Inserta en `deudas` (sin commit: decide el caller). Devuelve el reporte."""
    patron_notas = f"{PREFIJO_NOTA_EXCEL}%"
    borradas = 0
    if reemplazar:
        borradas = conn.execute("DELETE FROM deudas WHERE notas LIKE ?;", (patron_notas,)).rowcount
    monedas = {fila["codigo"]: fila for fila in conn.execute("SELECT id, codigo, decimales FROM monedas;")}
    ya_importadas = {fila["notas"] for fila in conn.execute("SELECT notas FROM deudas WHERE notas LIKE ?;", (patron_notas,))}
    salteadas: Counter = Counter()
    avisos: Counter = Counter()
    errores: list[str] = []
    insertadas: Counter = Counter()
    netos: dict[tuple[str, str], int] = defaultdict(int)

    for numero_linea, celdas in filas:
        persona = normalizar_persona(celdas[COL_PERSONA])
        if not persona:
            salteadas["sin PERSONA"] += 1
            continue
        nota = NOTA.format(n=numero_linea)
        if nota in ya_importadas:
            salteadas["ya importada (misma nota)"] += 1
            continue
        movimientos = _movimientos(celdas, avisos)
        if not movimientos:
            salteadas["sin monto"] += 1
            continue

        codigo_excel = _clave(celdas[COL_MONEDA])
        if not codigo_excel:
            avisos[f"MONEDA vacía → {MONEDA_SI_VACIA}"] += 1
            codigo_excel = MONEDA_SI_VACIA
        codigo = MAPA_MONEDA.get(codigo_excel)
        if codigo is None or codigo not in monedas:
            salteadas[f"moneda desconocida ({codigo_excel})"] += 1
            continue
        fecha = _fecha(celdas[COL_FECHA])
        if fecha is None:
            errores.append(f"línea {numero_linea}: fecha inválida {celdas[COL_FECHA]!r} — salteada")
            continue

        moneda = monedas[codigo]
        concepto = (celdas[COL_CONCEPTO] or "").strip() or None
        for valor in movimientos:
            monto_minor = round(valor * 10 ** moneda["decimales"])
            if monto_minor == 0:
                errores.append(f"línea {numero_linea}: monto {valor!r} redondea a 0 — salteado")
                continue
            conn.execute(
                """
                INSERT INTO deudas (entidad_persona, concepto, tab, monto_minor, moneda_id, fecha, notas, origen_tipo)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'manual');
                """,
                (persona, concepto, TAB, monto_minor, moneda["id"], fecha, nota),
            )
            insertadas["positivas" if monto_minor > 0 else "negativas"] += 1
            netos[(persona, codigo)] += monto_minor

    return {
        "borradas": borradas, "insertadas": insertadas, "salteadas": salteadas, "avisos": avisos,
        "errores": errores, "netos": netos,
    }


def _reporte(reporte: dict, filas_csv: int, reemplazar: bool) -> None:
    insertadas = reporte["insertadas"]
    print(f"\n[CSV] {filas_csv} fila(s) de datos")
    if reemplazar:
        print(f"[REEMPLAZAR] {reporte['borradas']} fila(s) importadas antes desde el Excel, borradas para volver a importar")
    print(f"[IMPORTADAS] {sum(insertadas.values())} fila(s) en ME DEBEN: "
          f"{insertadas['positivas']} positivas (te deben), {insertadas['negativas']} negativas (pagos que te hicieron)")
    for motivo, cantidad in sorted(reporte["salteadas"].items()):
        print(f"  ⏭️  salteadas — {motivo}: {cantidad}")
    for aviso, cantidad in sorted(reporte["avisos"].items()):
        print(f"  ⚠️  {aviso}: {cantidad}")
    for error in reporte["errores"]:
        print(f"  ❌ {error}")
    if reporte["netos"]:
        print("\n[NETO IMPORTADO POR PERSONA — ME DEBEN] (+ te debe, − te pagó de más)")
        for (persona, codigo), neto in sorted(reporte["netos"].items()):
            print(f"  {persona:<30} {neto / 100:>14,.2f} {codigo}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Importa la TABLA DEUDAS del Excel (CSV) al libro de deudas (ME DEBEN).")
    parser.add_argument("--csv", default=None, help=f"Ruta al CSV (default: {_mostrar_ruta(CSV_DEFAULT)}).")
    parser.add_argument("--db-path", default=None, help=f"Ruta a la DB (default: {_mostrar_ruta(DB_PATH)}).")
    parser.add_argument("--confirmar", action="store_true", help="Aplica los cambios. Sin esto, solo dry-run.")
    parser.add_argument(
        "--reemplazar", action="store_true",
        help="Borra las filas importadas antes desde el Excel y las vuelve a importar con las reglas actuales.",
    )
    args = parser.parse_args()

    csv_path = Path(args.csv).expanduser() if args.csv else CSV_DEFAULT
    db_path = Path(args.db_path) if args.db_path else DB_PATH
    if db_path.resolve() == DB_PROHIBIDA.resolve():
        sys.exit(f"❌ Este script nunca corre contra {_mostrar_ruta(DB_PROHIBIDA)}.")
    if not csv_path.exists():
        sys.exit(f"❌ No se encontró el CSV en {_mostrar_ruta(csv_path)} (pasá otra ruta con --csv).")
    if not db_path.exists():
        sys.exit(f"❌ No existe la base {_mostrar_ruta(db_path)}.")

    modo = "APLICANDO CAMBIOS" if args.confirmar else "DRY-RUN (la base real no se toca)"
    print(f"[INICIO] Migración de la TABLA DEUDAS — {modo}{' — REEMPLAZANDO LO YA IMPORTADO' if args.reemplazar else ''}")
    print(f"[CSV] {_mostrar_ruta(csv_path)}")
    print(f"[DB] {_mostrar_ruta(db_path)}")
    filas = _leer_csv(csv_path)

    if not args.confirmar:
        with tempfile.TemporaryDirectory(prefix="deltabalance_deudas_") as carpeta:
            copia = Path(carpeta) / "copia.db"
            origen, destino = sqlite3.connect(db_path), sqlite3.connect(copia)
            try:
                origen.backup(destino)  # copia consistente, incluido lo que esté en el -wal
            finally:
                destino.close()
                origen.close()
            db = DatabaseManager(copia)
            try:
                db.inicializar()  # en la copia: convierte `deudas` a la estructura final si hace falta
                conn = db.conn
                conn.commit()
                conn.execute("BEGIN;")
                reporte = _importar(conn, filas, args.reemplazar)
                conn.rollback()
            finally:
                db.desconectar()
        _reporte(reporte, len(filas), args.reemplazar)
        print("\nNada se escribió en la base real — revisá el reporte y corré de nuevo con --confirmar para aplicar.")
        return

    db = DatabaseManager(db_path)
    try:
        db.inicializar()  # convierte `deudas` a la estructura final si todavía no la tiene (con su propio backup)
        conn = db.conn
        # inicializar() puede dejar abierta una transacción implícita (backfill de una migración de columna).
        conn.commit()
        # El backup copia solo el archivo .db — con WAL, lo que siga en el -wal no estaría sin este checkpoint.
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
        db.hacer_backup()
        conn.execute("BEGIN;")
        try:
            reporte = _importar(conn, filas, args.reemplazar)
            conn.commit()
        except Exception:
            conn.rollback()
            print("\n❌ ERROR — rollback completo, no se importó nada.")
            raise
    finally:
        db.desconectar()
    _reporte(reporte, len(filas), args.reemplazar)
    print("\n✅ Deudas importadas. Recalculá los snapshots (↻ en Deudas) para que el SALDO ANTERIOR las incluya.")


if __name__ == "__main__":
    main()
