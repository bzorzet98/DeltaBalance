"""
DeltaBalance — migration/migrar_compras_cuotas.py

Importa TODAS las compras en cuotas de la hoja "REGISTRO COMPRA EN
CUOTAS" de la planilla vieja (migration/sources/, ver el README.md de esa
carpeta) a data/deltabalanceBZ.db — las ya pagadas del todo también, como
historial, con todas sus cuotas en 'pagado'. SQL directo vía
DatabaseManager — no pasa por FeesService ni por ningún service (pedido
explícito): el cronograma mes a mes que armaría FeesService se replica
acá, en la capa de normalización propia del script
(docs/MIGRATION_NOTES.md, principio 3).

Pasos:
1. Cuentas débito (CUENTAS_DEBITO) + su cuentas_saldos en ARS.
2. Tarjetas de crédito (CUENTAS_CREDITO), vinculadas a su débito vía
   cuenta_pago_id, + su cuentas_saldos en ARS.
3. Categorías faltantes (CATEGORIAS_NUEVAS).
4. Filas de la hoja → compras_cuotas + cuotas_credito.

--- Decisiones tomadas al relevar la hoja real ---

- Cronograma desde FECHA PRIMER PAGO, no desde FECHA COMPRA
  (PROYECTAR_DESDE_PRIMER_PAGO). La planilla guarda cuándo vence la
  primera cuota y, en casi todas las filas, es el mes SIGUIENTE a la
  compra o más tarde: proyectar desde la fecha de compra correría todo el
  cronograma un mes o más. Sin FECHA PRIMER PAGO válida, se usa FECHA
  COMPRA y se avisa.
- Las primeras CANTIDAD DE CUOTAS PAGADAS cuotas entran como 'pagado', el
  resto como 'pendiente' (MARCAR_CUOTAS_YA_PAGADAS) — por cantidad,
  contando desde la primera cuota del cronograma. Una compra ya pagada del
  todo entra con todas sus cuotas en 'pagado'; una a medio pagar, con las
  primeras en 'pagado'. Insertarlas todas como 'pendiente' las mostraría
  como deuda. FeesService.resumen_por_tarjeta() cuenta 'pagado' igual que
  'pendiente' (solo excluye 'omitido'), así que el total de cada mes no
  cambia por esto. Efecto buscado: esas compras quedan con la edición de
  monto/banco/moneda/cuotas bloqueada (CLAUDE.md §4) — sus cuotas pagadas
  ya pasaron por un resumen real.
- Monto <= 0 (reintegros/devoluciones en cuotas) entra tal cual, con monto
  y cuotas negativos — pedido explícito: así el total de cada mes del
  resumen refleja el reintegro. OJO: la app asume compras positivas
  (FeesService.create_purchase()/update_purchase() rechazan un total <= 0),
  así que el monto de estas compras no se puede editar desde la UI.
- Categorías y cuentas se comparan por nombre ignorando mayúsculas y
  tildes (el seed tiene 'Supermercado', no 'SUPERMERCADO'), y una que ya
  existe se reusa sin modificarla. Una categoría se busca solo entre las
  activas y por subcategoría, bajo cualquier categoría principal (ej.
  RENDIMIENTOS ya existe como 'MOVIMIENTO CAPITAL · Rendimientos'): la UI
  identifica las categorías solo por la subcategoría, así que un segundo
  "Rendimientos" sería indistinguible del primero.
- Si una compra viene en una moneda que su tarjeta todavía no opera (ej.
  USD), se agrega esa moneda a cuentas_saldos de la tarjeta, con saldo 0:
  una cuenta opera en las monedas de cuentas_saldos
  (docs/DATA_MODEL_DECISIONS.md §14) y la fila de alta de Compras en
  cuotas solo ofrece esas.
- Trazabilidad (docs/MIGRATION_NOTES.md, principio 2): cada compra
  importada lleva en `notas` "MIGRADO DESDE EXCEL — <HOJA>, FILA <N>".
- Idempotencia: si ya existe una compra con el mismo concepto +
  fecha_compra + cuenta_id + monto_total_minor, la fila se saltea —
  correr el script dos veces no duplica nada. Consecuencia: dos filas
  idénticas en la planilla se importan una sola vez.
- Todo texto insertado va en MAYÚSCULAS. Los valores de enum que exige
  un CHECK del schema ('debito', 'credito', 'pendiente', 'pagado', ...)
  quedan en minúscula, como los define db/schema.sql.

--- Transacción y seguridad ---

Todo (cuentas, categorías y filas) va en UNA sola transacción. Una fila
con datos inválidos se saltea y cuenta como error sin escribir nada; un
error de base de datos (o de otro tipo) hace rollback de TODO y corta el
script.

Mismo esquema que el resto de migration/ (ver
migrar_categorias_simplificadas.py): por default es DRY-RUN — corre todo
dentro de la transacción, imprime el reporte completo y al final hace
rollback. Con --confirmar hace backup de la DB y commitea. Nunca corre
contra data/deltabalance.db (se rechaza aunque se pase por --db-path).
DatabaseManager.inicializar() sí crea la DB si no existe (schema + seed),
igual que al abrir la app.

Requiere openpyxl (no está en environment.yml):
    pip install openpyxl

Uso:
    # 1) Dry-run (no escribe nada, solo reporta):
    python migration/migrar_compras_cuotas.py

    # 2) Aplicar de verdad (hace backup antes):
    python migration/migrar_compras_cuotas.py --confirmar

    # Opcional: contra otra DB (ej. una copia de prueba)
    python migration/migrar_compras_cuotas.py --db-path /tmp/prueba.db --confirmar

Este script NO lo ejecuta Claude Code (CLAUDE.md §0.1) — lo corre el
usuario a mano.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
import unicodedata
import warnings
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    import openpyxl
    from openpyxl.utils.datetime import from_excel
except ImportError:
    sys.exit("❌ Falta openpyxl — instalalo en el entorno del proyecto:  pip install openpyxl")

from db.database import DatabaseManager, to_minor

# openpyxl avisa por cada extensión de Excel que no soporta (tablas
# dinámicas, validaciones) — no afecta la lectura de valores.
warnings.filterwarnings("ignore", category=UserWarning, module="openpyxl")

RAIZ = Path(__file__).resolve().parent.parent
DB_PATH = RAIZ / "data" / "deltabalanceBZ.db"
DB_PROTEGIDA = RAIZ / "data" / "deltabalance.db"
SOURCES_DIR = Path(__file__).resolve().parent / "sources"
# El nombre pedido primero; después el nombre con el que se exporta hoy
# (con espacio). Se usa el primero que exista.
XLSX_CANDIDATOS = [SOURCES_DIR / "REGISTRO_PRINCIPAL.xlsx", SOURCES_DIR / "REGISTRO PRINCIPAL.xlsx"]
HOJA = "REGISTRO COMPRA EN CUOTAS"
FILAS_BUSQUEDA_HEADER = 20
MONEDA_DEFAULT = "ARS"
NOTA_MIGRACION = "MIGRADO DESDE EXCEL — {hoja}, FILA {fila}"

# Ver docstring del módulo, "Decisiones". En False: cronograma desde FECHA
# COMPRA / todas las cuotas 'pendiente' (mapeo literal).
PROYECTAR_DESDE_PRIMER_PAGO = True
MARCAR_CUOTAS_YA_PAGADAS = True

# --- Columnas de la hoja (fila de encabezados) ---
COL_CONCEPTO = "CONCEPTO"
COL_BANCO = "BANCO"
COL_SUBCATEGORIA = "SUBCATEGORIA"
COL_PRECIO_TOTAL = "PRECIO TOTAL"
COL_FECHA_COMPRA = "FECHA COMPRA"
COL_CUOTAS_TOTALES = "CANTIDAD DE CUOTAS TOTALES"
COL_MONEDA = "MONEDA"
COL_FECHA_PRIMER_PAGO = "FECHA PRIMER PAGO"
COL_CUOTAS_PAGADAS = "CANTIDAD DE CUOTAS PAGADAS"
# PAGADO no se lee: se importan todas las filas, y qué cuotas están pagadas
# lo dice CANTIDAD DE CUOTAS PAGADAS (PAGADO es la fórmula "pagadas =
# totales" de la planilla).
COLUMNAS_OBLIGATORIAS = (
    COL_CONCEPTO, COL_BANCO, COL_SUBCATEGORIA, COL_PRECIO_TOTAL,
    COL_FECHA_COMPRA, COL_CUOTAS_TOTALES, COL_MONEDA,
)
# Si faltan, se sigue con el mapeo literal (cronograma desde FECHA COMPRA,
# todas las cuotas 'pendiente' — ojo: sin CUOTAS PAGADAS, también las
# compras históricas quedarían como deuda) y se avisa.
COLUMNAS_OPCIONALES = (COL_FECHA_PRIMER_PAGO, COL_CUOTAS_PAGADAS)

# --- Paso 1 y 2: cuentas ---
CUENTAS_DEBITO = [
    "NACION", "CREDICOOP", "BBVA", "BILLETERA ER", "BINANCE",
    "BRUBANK", "BUENBIT", "BULL MARKET", "COCOS", "FIWIND",
    "GALICIA", "JOY", "MACRO", "MP", "NARANJA X", "NEXO", "SANTANDER",
]

CUENTAS_CREDITO = {
    "NACION MASTERCARD":      "NACION",
    "NACION VISA":            "NACION",
    "CREDICOOP CABAL":        "CREDICOOP",
    "CREDICOOP VISA":         "CREDICOOP",
    "CREDICOOP VISA EXT NOE": "CREDICOOP",
    "MERCADO PAGO CREDITO":   "MP",
}

# --- Paso 3: categorías ---
CATEGORIAS_NUEVAS = [
    ("EGRESOS VARIABLES", "ALIMENTOS",           "egreso"),
    ("EGRESOS VARIABLES", "GASTRONOMÍA",         "egreso"),
    # REGALOS y MASCOTAS por separado (no la 'Regalos y Mascotas' del seed).
    ("EGRESOS VARIABLES", "REGALOS",             "egreso"),
    ("EGRESOS VARIABLES", "MASCOTAS",            "egreso"),
    # Genérica: destino de CATEGORIA_FALLBACK.
    ("EGRESOS VARIABLES", "EGRESO VARIABLE",     "egreso"),
    ("EGRESOS VARIABLES", "VACACIONES",          "egreso"),
    ("EGRESOS VARIABLES", "REINTEGRO PROMOCION", "egreso"),
    ("INGRESOS",          "INGRESO VARIABLE",    "ingreso"),
    ("INGRESOS",          "BECA",                "ingreso"),
    ("INGRESOS",          "RENDIMIENTOS",        "ingreso"),
]

# --- Paso 4: mapeos (valor de la planilla → nombre en la DB) ---
BANCO_OTRO = "OTRO"  # → se saltea la fila
MAPA_BANCO = {
    "MASTERCARD":             "NACION MASTERCARD",
    "VISA":                   "NACION VISA",
    "CABAL CREDICOOP":        "CREDICOOP CABAL",
    "VISA CREDICOOP":         "CREDICOOP VISA",
    "VISA CREDICOOP EXT NOE": "CREDICOOP VISA EXT NOE",
    "CREDITO MP":             "MERCADO PAGO CREDITO",
}

MAPA_CATEGORIA = {
    "PROVEDURIA":          "ALIMENTOS",
    "COMIDAS Y BEBIDAS":   "ALIMENTOS",
    "ALCOHOL":             "GASTRONOMÍA",
    "SUPERMERCADO":        "SUPERMERCADO",
    "TRANSPORTE":          "TRANSPORTE / AUTO",
    "OCIO":                "OCIO",
    "PERSONAL":            "BIENESTAR Y DEPORTE",
    "REGALOS":             "REGALOS",
    "MASCOTAS":            "MASCOTAS",
    "HOGAR":               "HOGAR",
    "SALUD":               "SALUD",
    "VACACIONES":          "VACACIONES",
    "CUENTAS Y SERVICIOS": "SERVICIOS",
    "EGRESO FIJO":         "SERVICIOS",
    "REINTEGRO PROMOCION": "REINTEGRO PROMOCION",
    "DEVOLUCIÓN TARJETA":  "REINTEGRO",
    "DEVOLUCION TARJETA":  "REINTEGRO",
    "INGRESO VARIABLE":    "INGRESO VARIABLE",
    "DEUDA":               "COBRO DEUDA",
    "DEUDA NOE":           "DEUDA",  # MOVIMIENTO CAPITAL · Deuda (seed)
}

# Subcategoría fuera de MAPA_CATEGORIA (en la planilla: COMPARTIDO, EGRESO,
# EGRESO VARIABLE) → se infiere por palabras del concepto: primero
# INFERENCIA_KEYWORDS, después REGLAS_INFERENCIA, cada una en su orden; si
# nada coincide, CATEGORIA_FALLBACK (genérica, se crea en
# CATEGORIAS_NUEVAS).

# Por PREFIJO de palabra ("LAVARROP" → LAVARROPAS, "BULONER" → BULONERA).
# Van primero porque nombran lo que se compró, más específico que el
# comercio de REGLAS_INFERENCIA ("HELADERA COTO" es HOGAR, no SUPERMERCADO).
INFERENCIA_KEYWORDS: dict[str, list[str]] = {
    "HOGAR": ["LAVARROP", "HELADER", "COLCHON", "ESTANTE", "MUEBLE", "SOPORTE",
              "CORTINA", "ORGANIZ", "FREIDORA", "ROBOT", "FIERRO", "HIERRO",
              "BULONER", "CARRITO", "CORTADOR", "GADNIC"],
    "TRANSPORTE / AUTO": ["MULTA", "COMBUSTIBLE"],
    "SERVICIOS": ["CABLE", "FLOW", "MANTENIMIENTO"],
    "GASTRONOMÍA": ["ASADO", "FRIAR", "PORCA", "JUNTADA", "CITA"],
    # "PERSONAL" es la subcategoría de la planilla, no una categoría de la
    # DB: MAPA_CATEGORIA la manda a BIENESTAR Y DEPORTE.
    "BIENESTAR Y DEPORTE": ["LEGALIZ"],
}

# Por palabra COMPLETA (así "DIA" no coincide con "MEDIA" ni "GAS" con
# "GASTOS", que como prefijo sí coincidirían).
REGLAS_INFERENCIA: list[tuple[str, tuple[str, ...]]] = [
    ("ALQUILER / VIVIENDA", ("ALQUILER", "EXPENSA", "EXPENSAS", "GAS", "LUZ")),
    ("SUPERMERCADO",        ("SUPER", "SUPERMERCADO", "MERCADO", "DIA", "COTO", "CARREFOUR")),
    ("ALIMENTOS",           ("COMIDA", "COMIDAS", "RESTO", "RESTAURANT", "RESTAURANTE", "DELIVERY")),
]
# "MERCADO LIBRE" / "MERCADO PAGO" no son un supermercado.
PALABRAS_QUE_ANULAN_MERCADO = {"LIBRE", "PAGO"}
CATEGORIA_FALLBACK = "EGRESO VARIABLE"


class MigracionError(Exception):
    """Problema estructural (falta la hoja, una columna, la moneda default) — corta el script."""


# ============================================================
# NORMALIZACIÓN
# ============================================================

def _texto(valor: Any) -> str:
    """strip + MAYÚSCULAS. None → ''."""
    if valor is None:
        return ""
    return str(valor).strip().upper()


def _clave(texto: str) -> str:
    """Clave de comparación: sin tildes, en mayúsculas, espacios colapsados ('Gastronomía' == 'GASTRONOMIA')."""
    sin_tildes = "".join(c for c in unicodedata.normalize("NFD", texto) if unicodedata.category(c) != "Mn")
    return " ".join(sin_tildes.upper().split())


def _parsear_fecha(valor: Any) -> Optional[str]:
    """datetime/date de openpyxl, serial de Excel, 'DD/MM/YYYY', 'D/M/YYYY' o 'YYYY-MM-DD' → 'YYYY-MM-DD'."""
    if valor is None or isinstance(valor, bool):
        return None
    if isinstance(valor, datetime):
        return valor.date().isoformat()
    if isinstance(valor, date):
        return valor.isoformat()
    if isinstance(valor, (int, float)):
        # Serial de Excel en una celda sin formato de fecha.
        try:
            return from_excel(valor).date().isoformat()
        except (AttributeError, OverflowError, TypeError, ValueError):
            return None
    texto = str(valor).strip()
    for formato in ("%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d", "%d-%m-%Y"):
        try:
            return datetime.strptime(texto, formato).date().isoformat()
        except ValueError:
            continue
    return None


def _parsear_monto(valor: Any) -> Optional[float]:
    """Número de openpyxl, o texto tipo '$150.000,50' / '150,000.50' / '150000'."""
    if valor is None or isinstance(valor, bool):
        return None
    if isinstance(valor, (int, float)):
        return float(valor)
    texto = str(valor).strip().upper()
    for simbolo in ("U$S", "USD", "ARS", "$"):
        texto = texto.replace(simbolo, "")
    texto = texto.replace(" ", "")
    if not texto:
        return None
    if "," in texto and "." in texto:
        # El separador que aparece último es el decimal.
        if texto.rfind(",") > texto.rfind("."):
            texto = texto.replace(".", "").replace(",", ".")
        else:
            texto = texto.replace(",", "")
    elif "," in texto:
        texto = texto.replace(",", ".")
    elif texto.count(".") > 1:
        texto = texto.replace(".", "")  # 1.234.567 → separador de miles
    try:
        return float(texto)
    except ValueError:
        return None


def _parsear_entero(valor: Any) -> Optional[int]:
    """12 / 12.0 / '12' → 12. Un número con decimales reales (2.5) no es una cantidad de cuotas válida."""
    if valor is None or isinstance(valor, bool):
        return None
    try:
        numero = float(valor) if isinstance(valor, (int, float)) else float(str(valor).strip().replace(",", "."))
    except ValueError:
        return None
    if numero != int(numero):
        return None
    return int(numero)


def _sumar_meses(anio: int, mes: int, meses: int) -> tuple[int, int]:
    indice = anio * 12 + (mes - 1) + meses
    return indice // 12, indice % 12 + 1


def _inferir_categoria(concepto: str) -> Optional[str]:
    palabras = set(_clave(concepto).replace("/", " ").replace("-", " ").split())
    for destino, prefijos in INFERENCIA_KEYWORDS.items():
        if any(palabra.startswith(prefijo) for palabra in palabras for prefijo in prefijos):
            return destino
    for destino, claves in REGLAS_INFERENCIA:
        for clave in claves:
            if clave not in palabras:
                continue
            if clave == "MERCADO" and palabras & PALABRAS_QUE_ANULAN_MERCADO:
                continue
            return destino
    return None


# ============================================================
# PASOS 1-3: CUENTAS Y CATEGORÍAS
# ============================================================

def _asegurar_cuenta(
    conn: sqlite3.Connection,
    nombre: str,
    tipo: str,
    moneda_ars_id: int,
    cuenta_pago_id: Optional[int] = None,
    nombre_debito: Optional[str] = None,
) -> int:
    """Crea la cuenta (+ su cuentas_saldos en ARS) si no existe. Una que ya existe se devuelve sin tocarla."""
    existentes = {_clave(r["nombre"]): r for r in conn.execute("SELECT id, nombre, tipo, cuenta_pago_id FROM cuentas;")}
    existente = existentes.get(_clave(nombre))
    if existente is not None:
        como = f" (como '{existente['nombre']}')" if existente["nombre"] != nombre else ""
        print(f"  ⏭️  {nombre} ya existe{como} — no se modifica")
        if existente["tipo"] != tipo:
            print(f"      ⚠️  su tipo es '{existente['tipo']}', no '{tipo}'")
        if cuenta_pago_id is not None and existente["cuenta_pago_id"] != cuenta_pago_id:
            print(f"      ⚠️  no está vinculada a {nombre_debito} (cuenta_pago_id={existente['cuenta_pago_id']})")
        return existente["id"]

    conn.execute(
        "INSERT OR IGNORE INTO cuentas (nombre, tipo, cuenta_pago_id) VALUES (?, ?, ?);",
        (nombre, tipo, cuenta_pago_id),
    )
    cuenta_id = conn.execute("SELECT id FROM cuentas WHERE nombre = ?;", (nombre,)).fetchone()["id"]
    conn.execute(
        "INSERT OR IGNORE INTO cuentas_saldos (cuenta_id, moneda_id, saldo_inicial_minor) VALUES (?, ?, 0);",
        (cuenta_id, moneda_ars_id),
    )
    vinculo = f" → vinculada a {nombre_debito}" if nombre_debito else ""
    print(f"  ✅ {nombre} creada{vinculo}")
    return cuenta_id


def _crear_cuentas(conn: sqlite3.Connection, moneda_ars_id: int) -> dict[str, int]:
    ids: dict[str, int] = {}
    print("\n[CUENTAS DÉBITO]")
    for nombre in CUENTAS_DEBITO:
        ids[nombre] = _asegurar_cuenta(conn, nombre, "debito", moneda_ars_id)
    print("\n[CUENTAS CRÉDITO]")
    for nombre, debito in CUENTAS_CREDITO.items():
        ids[nombre] = _asegurar_cuenta(
            conn, nombre, "credito", moneda_ars_id, cuenta_pago_id=ids[debito], nombre_debito=debito,
        )
    return ids


def _categorias_activas(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT id, categoria_principal, subcategoria, tipo FROM categorias WHERE activa = 1;"
    ).fetchall()


def _crear_categorias(conn: sqlite3.Connection) -> None:
    print("\n[CATEGORÍAS]")
    for principal, sub, tipo in CATEGORIAS_NUEVAS:
        existente = next(
            (c for c in _categorias_activas(conn) if _clave(c["subcategoria"]) == _clave(sub)), None,
        )
        if existente is not None:
            ubicacion = f"{existente['categoria_principal']} · {existente['subcategoria']}"
            como = "" if ubicacion == f"{principal} · {sub}" else f" (como '{ubicacion}' — se reusa, no se duplica)"
            print(f"  ⏭️  {sub} ya existe{como}")
            if existente["tipo"] != tipo:
                print(f"      ⚠️  su tipo es '{existente['tipo']}', no '{tipo}'")
            continue

        cur = conn.execute(
            "INSERT OR IGNORE INTO categorias (categoria_principal, subcategoria, tipo) VALUES (?, ?, ?);",
            (principal, sub, tipo),
        )
        if cur.rowcount == 0:
            # Existe con ese nombre exacto pero desactivada: no se revierte
            # un soft-delete hecho a propósito — las filas que la
            # necesiten quedan como error.
            print(f"  ⚠️  {principal} · {sub} existe DESACTIVADA — no se reactiva")
        else:
            print(f"  ✅ {sub} creada")


def _mapa_categorias(conn: sqlite3.Connection) -> dict[str, sqlite3.Row]:
    """Categorías activas por clave de subcategoría. Si una subcategoría se repite bajo dos principales, gana la de tipo 'egreso' (son compras)."""
    mapa: dict[str, sqlite3.Row] = {}
    for c in _categorias_activas(conn):
        clave = _clave(c["subcategoria"])
        previa = mapa.get(clave)
        if previa is None or (previa["tipo"] != "egreso" and c["tipo"] == "egreso"):
            mapa[clave] = c
    return mapa


# ============================================================
# PASO 4: FILAS
# ============================================================

def _encontrar_header(ws) -> tuple[int, dict[str, int]]:
    for numero, valores in enumerate(
        ws.iter_rows(min_row=1, max_row=FILAS_BUSQUEDA_HEADER, values_only=True), start=1,
    ):
        textos = [_texto(v) for v in valores]
        if COL_CONCEPTO in textos and COL_BANCO in textos:
            columnas: dict[str, int] = {}
            for indice, texto in enumerate(textos):
                if texto:
                    columnas.setdefault(texto, indice)
            faltantes = [c for c in COLUMNAS_OBLIGATORIAS if c not in columnas]
            if faltantes:
                raise MigracionError(f"A la hoja '{HOJA}' le faltan columnas: {', '.join(faltantes)}.")
            return numero, columnas
    raise MigracionError(
        f"No se encontró la fila de encabezados (con {COL_CONCEPTO} y {COL_BANCO}) "
        f"en las primeras {FILAS_BUSQUEDA_HEADER} filas de '{HOJA}'."
    )


def _procesar_filas(
    conn: sqlite3.Connection,
    xlsx_path: Path,
    ids_cuentas: dict[str, int],
    categorias: dict[str, sqlite3.Row],
    monedas: dict[str, sqlite3.Row],
) -> dict[str, int]:
    cont = {
        "total": 0, "importadas": 0, "importadas_todas_pagadas": 0, "ya_existian": 0,
        "saltadas_banco": 0, "errores": 0, "inferidas": 0,
    }
    mapa_banco = {_clave(k): v for k, v in MAPA_BANCO.items()}
    mapa_categoria = {_clave(k): v for k, v in MAPA_CATEGORIA.items()}

    print(f"\n[FILAS] Procesando hoja {HOJA}...")
    wb = openpyxl.load_workbook(xlsx_path, read_only=True, data_only=True)
    try:
        if HOJA not in wb.sheetnames:
            raise MigracionError(f"La planilla no tiene la hoja '{HOJA}'.")
        ws = wb[HOJA]
        fila_header, columnas = _encontrar_header(ws)
        for col in COLUMNAS_OPCIONALES:
            if col not in columnas:
                print(f"  ⚠️  La hoja no tiene la columna '{col}' — se sigue sin ella (ver docstring).")

        for fila_excel, valores in enumerate(
            ws.iter_rows(min_row=fila_header + 1, values_only=True), start=fila_header + 1,
        ):
            def celda(col: str) -> Any:
                indice = columnas.get(col)
                return valores[indice] if indice is not None and indice < len(valores) else None

            concepto = _texto(celda(COL_CONCEPTO))
            if not concepto:
                continue  # fila vacía (la hoja tiene formato aplicado más abajo de los datos)
            cont["total"] += 1
            etiqueta = f"[{fila_excel:03d}] {concepto}"

            def error(motivo: str) -> None:
                cont["errores"] += 1
                print(f"  ❌ {etiqueta} | {motivo} — salteando")

            cuotas_totales = _parsear_entero(celda(COL_CUOTAS_TOTALES))
            cuotas_pagadas = _parsear_entero(celda(COL_CUOTAS_PAGADAS)) or 0

            # --- Banco ---
            banco = _texto(celda(COL_BANCO))
            if banco == BANCO_OTRO:
                cont["saltadas_banco"] += 1
                print(f"  ⏭️  {etiqueta} | BANCO=OTRO — salteando")
                continue
            nombre_tarjeta = mapa_banco.get(_clave(banco))
            if nombre_tarjeta is None:
                cont["saltadas_banco"] += 1
                print(f'  ⚠️  {etiqueta} | BANCO DESCONOCIDO: "{banco}" — salteando')
                continue
            cuenta_id = ids_cuentas[nombre_tarjeta]

            # --- Monto, fecha, cuotas ---
            precio = _parsear_monto(celda(COL_PRECIO_TOTAL))
            if precio is None:
                error(f"PRECIO TOTAL INVÁLIDO: {celda(COL_PRECIO_TOTAL)!r}")
                continue

            fecha_compra = _parsear_fecha(celda(COL_FECHA_COMPRA))
            if fecha_compra is None:
                error(f"FECHA COMPRA INVÁLIDA: {celda(COL_FECHA_COMPRA)!r}")
                continue

            if cuotas_totales is None:
                error(f"CANTIDAD DE CUOTAS TOTALES INVÁLIDA: {celda(COL_CUOTAS_TOTALES)!r}")
                continue
            avisos: list[str] = []
            if cuotas_totales < 1:
                avisos.append(f"CANTIDAD DE CUOTAS {cuotas_totales} → 1")
                cuotas_totales = 1
            cuotas_pagadas = max(0, min(cuotas_pagadas, cuotas_totales))

            # --- Categoría: mapa → inferencia por concepto → fallback ---
            subcategoria = _texto(celda(COL_SUBCATEGORIA))
            destino = mapa_categoria.get(_clave(subcategoria))
            if destino is None:
                cont["inferidas"] += 1
                original = subcategoria or "(vacía)"
                inferida = _inferir_categoria(concepto)
                if inferida is not None:
                    destino = inferida
                    avisos.append(f"CATEGORÍA INFERIDA: {destino} (original: {original})")
                else:
                    destino = CATEGORIA_FALLBACK
                    avisos.append(
                        f"CATEGORÍA INFERIDA: {destino} (original: {original} — sin pistas en el concepto, fallback)"
                    )
            categoria = categorias.get(_clave(destino))
            if categoria is None:
                error(f"LA CATEGORÍA DESTINO '{destino}' NO EXISTE (ACTIVA) EN LA DB")
                continue
            if categoria["tipo"] != "egreso":
                avisos.append(f"CATEGORÍA DE TIPO '{categoria['tipo']}' EN UNA COMPRA ({destino}) — revisar")

            # --- Moneda ---
            moneda_txt = _texto(celda(COL_MONEDA))
            moneda = monedas.get(_clave(moneda_txt))
            if moneda is None:
                moneda = monedas[_clave(MONEDA_DEFAULT)]
                avisos.append(f"MONEDA DESCONOCIDA: \"{moneda_txt or '(vacía)'}\" → {MONEDA_DEFAULT}")

            monto_total_minor = to_minor(precio, moneda["decimales"])
            monto_por_cuota_minor = round(monto_total_minor / cuotas_totales)

            # --- Idempotencia ---
            existente = conn.execute(
                """
                SELECT id FROM compras_cuotas
                WHERE concepto = ? AND fecha_compra = ? AND cuenta_id = ? AND monto_total_minor = ?
                LIMIT 1;
                """,
                (concepto, fecha_compra, cuenta_id, monto_total_minor),
            ).fetchone()
            if existente is not None:
                cont["ya_existian"] += 1
                print(f"  ⏭️  {etiqueta} | YA EXISTE (compra #{existente['id']}) — salteando")
                continue

            # --- Cronograma ---
            base_cronograma = fecha_compra
            if PROYECTAR_DESDE_PRIMER_PAGO:
                primer_pago = _parsear_fecha(celda(COL_FECHA_PRIMER_PAGO))
                if primer_pago is not None:
                    base_cronograma = primer_pago
                elif COL_FECHA_PRIMER_PAGO in columnas:
                    avisos.append("SIN FECHA PRIMER PAGO VÁLIDA — cuotas proyectadas desde FECHA COMPRA")
            anio_base, mes_base = int(base_cronograma[:4]), int(base_cronograma[5:7])

            # --- La tarjeta tiene que operar en la moneda de la compra ---
            cur = conn.execute(
                "INSERT OR IGNORE INTO cuentas_saldos (cuenta_id, moneda_id, saldo_inicial_minor) VALUES (?, ?, 0);",
                (cuenta_id, moneda["id"]),
            )
            if cur.rowcount:
                avisos.append(f"{nombre_tarjeta} AHORA OPERA TAMBIÉN EN {moneda['codigo']} (cuentas_saldos)")

            # --- Insert: compra + cuotas ---
            cur = conn.execute(
                """
                INSERT INTO compras_cuotas
                    (fecha_compra, concepto, cuenta_id, categoria_id, moneda_id,
                     monto_total_minor, total_cuotas, monto_por_cuota_minor, notas)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    fecha_compra, concepto, cuenta_id, categoria["id"], moneda["id"],
                    monto_total_minor, cuotas_totales, monto_por_cuota_minor,
                    NOTA_MIGRACION.format(hoja=HOJA, fila=fila_excel),
                ),
            )
            compra_id = cur.lastrowid
            for numero in range(1, cuotas_totales + 1):
                anio, mes = _sumar_meses(anio_base, mes_base, numero - 1)
                estado = "pagado" if (MARCAR_CUOTAS_YA_PAGADAS and numero <= cuotas_pagadas) else "pendiente"
                conn.execute(
                    """
                    INSERT INTO cuotas_credito
                        (compra_id, numero_cuota, mes_proyectado, anio_proyectado, monto_cuota_minor, estado)
                    VALUES (?, ?, ?, ?, ?, ?);
                    """,
                    (compra_id, numero, mes, anio, monto_por_cuota_minor, estado),
                )

            cont["importadas"] += 1
            todas_pagadas = MARCAR_CUOTAS_YA_PAGADAS and cuotas_pagadas == cuotas_totales
            if todas_pagadas:
                cont["importadas_todas_pagadas"] += 1
            for aviso in avisos:
                print(f"  ⚠️  {etiqueta} | {aviso}")
            if todas_pagadas:
                ya_pagadas = " (TODAS PAGADAS)"
            elif MARCAR_CUOTAS_YA_PAGADAS and cuotas_pagadas:
                ya_pagadas = f" ({cuotas_pagadas} YA PAGADAS)"
            else:
                ya_pagadas = ""
            print(
                f"  ✅ {etiqueta} | {nombre_tarjeta} | {moneda['codigo']} ${precio:,.2f} | "
                f"{cuotas_totales} CUOTAS{ya_pagadas} | {fecha_compra} | 1ª CUOTA {mes_base:02d}/{anio_base}"
            )
    finally:
        wb.close()
    return cont


# ============================================================
# MAIN
# ============================================================

def _mostrar_ruta(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(RAIZ))
    except ValueError:
        return str(path)


def main() -> None:
    parser = argparse.ArgumentParser(description="Migra las compras en cuotas desde la planilla Excel.")
    parser.add_argument("--db-path", default=None, help=f"Ruta a la DB (default: {_mostrar_ruta(DB_PATH)}).")
    parser.add_argument("--confirmar", action="store_true", help="Aplica los cambios. Sin esto, solo dry-run.")
    args = parser.parse_args()

    db_path = Path(args.db_path) if args.db_path else DB_PATH
    if db_path.resolve() == DB_PROTEGIDA.resolve():
        sys.exit(f"❌ Este script nunca corre contra {_mostrar_ruta(DB_PROTEGIDA)} (la DB real de la app).")

    xlsx_path = next((p for p in XLSX_CANDIDATOS if p.exists()), None)
    if xlsx_path is None:
        buscados = ", ".join(f"'{p.name}'" for p in XLSX_CANDIDATOS)
        sys.exit(f"❌ No se encontró la planilla en {_mostrar_ruta(SOURCES_DIR)} (se buscó {buscados}). Ver su README.md.")

    modo = "APLICANDO CAMBIOS" if args.confirmar else "DRY-RUN (nada se escribe)"
    print(f"[INICIO] Migración de compras en cuotas — {modo}")
    print(f"[XLSX] {_mostrar_ruta(xlsx_path)}")
    print(f"[DB] Conectando a {_mostrar_ruta(db_path)}")

    db = DatabaseManager(db_path)
    db.inicializar()  # idempotente: schema + migraciones de columna; seed solo si la DB es nueva
    conn = db.conn
    # inicializar() puede dejar abierta una transacción implícita (el
    # backfill de una migración de columna recién aplicada no commitea) —
    # se cierra acá para que el rollback del dry-run o de un error deshaga
    # SOLO lo de este script, nunca ese backfill.
    conn.commit()

    if args.confirmar:
        # El backup copia solo el archivo .db — con journal_mode=WAL, lo
        # que siga en el -wal no estaría en la copia sin este checkpoint.
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
        db.hacer_backup()

    try:
        monedas = {_clave(m["codigo"]): m for m in conn.execute("SELECT id, codigo, decimales FROM monedas;")}
        moneda_ars = monedas.get(_clave(MONEDA_DEFAULT))
        if moneda_ars is None:
            raise MigracionError(f"La moneda default {MONEDA_DEFAULT} no existe en la DB.")

        ids_cuentas = _crear_cuentas(conn, moneda_ars["id"])
        _crear_categorias(conn)
        categorias = _mapa_categorias(conn)

        destinos = (
            set(MAPA_CATEGORIA.values()) | set(INFERENCIA_KEYWORDS)
            | {d for d, _ in REGLAS_INFERENCIA} | {CATEGORIA_FALLBACK}
        )
        faltantes = sorted(d for d in destinos if _clave(d) not in categorias)
        if faltantes:
            print(f"\n  ⚠️  Categorías destino que NO existen activas en la DB (sus filas van a Errores): {', '.join(faltantes)}")

        cont = _procesar_filas(conn, xlsx_path, ids_cuentas, categorias, monedas)
    except Exception as err:
        conn.rollback()
        db.desconectar()
        print(f"\n❌ ERROR — rollback completo, no se escribió nada: {err}")
        if isinstance(err, MigracionError):
            sys.exit(1)
        raise

    if args.confirmar:
        conn.commit()
    else:
        conn.rollback()
    db.desconectar()

    print(f"\n[FIN] {modo}")
    print(f"  Total filas procesadas : {cont['total']}")
    print(f"  Importadas             : {cont['importadas']}")
    print(f"    con todas las cuotas pagadas (históricas) : {cont['importadas_todas_pagadas']}")
    print(f"  Ya existían            : {cont['ya_existian']}")
    print(f"  Saltadas (OTRO/desc.)  : {cont['saltadas_banco']}")
    print(f"  Errores                : {cont['errores']}")
    print(f"  Categorías inferidas   : {cont['inferidas']} (revisar las líneas ⚠️ CATEGORÍA INFERIDA)")
    if not args.confirmar:
        print("\nNada se escribió — revisá el reporte y corré de nuevo con --confirmar para aplicar.")


if __name__ == "__main__":
    main()
