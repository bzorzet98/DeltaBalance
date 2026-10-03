"""
DeltaBalance — migration/migrar_egresos.py

Importa la TABLA EGRESOS del Excel viejo (exportada a CSV) a la tabla
`presupuestos` en su estructura final (db/schema_migrations.py,
reestructurar_presupuestos_ingresos(): tipos 'fijo' y 'variable').

CSV esperado (por POSICIÓN de columna — la 4ª no tiene nombre):
    CONCEPTO, CATEGORIA, PRECIO, [columna negativa], MONEDA, FECHA
Default: migration/sources/REGISTRO PRINCIPAL - TABLA EGRESOS (2).csv (la
carpeta sources/ está en .gitignore); otra ruta con --csv. Lectura, moneda,
período y el armado dry-run / --confirmar son los de migrar_ingresos.py.

Reglas (pedido explícito):
- CATEGORIA 'EGRESOS FIJOS' → tipo='fijo', concepto = CONCEPTO,
  es_recurrente = 1.
- CATEGORIA 'EGRESOS VARIABLES' → tipo='variable' con la categoría que
  corresponda al CONCEPTO (ver abajo), es_recurrente = 0. Si no se
  encuentra ninguna → tipo='fijo' con concepto = CONCEPTO (fallback),
  también con es_recurrente = 0 (sigue siendo un egreso variable del Excel).
- CATEGORIA 'CANCELACIÓN' → se saltea. Otra CATEGORIA → se saltea y se
  informa.
- PRECIO positivo → monto_estimado_minor. Sin PRECIO positivo, la fila se
  saltea (no hay nada que presupuestar). La columna negativa se ignora:
  el real de un variable se calcula del Registro, y el de un fijo se carga
  después en la app.
- FECHA → mes y anio. MONEDA: MAPA_MONEDA (ARS, USD); vacía → ARS.
- Concepto: sin espacios de más y en MAYÚSCULAS (la misma regla que
  PresupuestosService). Se saltean las filas sin CONCEPTO, las de título /
  encabezado (CONCEPTO 'CONCEPTO' o 'EGRESOS') y los reintegros /
  acreditaciones de promociones (concepto que empieza con uno de
  PREFIJOS_SALTEAR, cualquiera sea su CATEGORIA).

Categoría de un EGRESO VARIABLE (_Categorias.resolver()), en este orden;
el dry-run lista cómo quedó cada concepto, para revisarlo antes de
--confirmar:
1. MAPA_VARIABLE: una clave vale para el concepto exacto o como sus
   primeras palabras ('TARJETA' → 'TARJETA CUMPLE ROXI'; gana la clave
   más larga). Si la categoría del mapa no existe (o está desactivada) →
   fallback a fijo, y se informa. Las del mapa pueden ser de cualquier
   tipo (INVERSIONES y AHORRO/INVERSIÓN son de 'movimiento').
2. "Nombre similar" (pedido: buscar en categorias por nombre similar al
   CONCEPTO), solo entre las categorías de egreso activas — las que ofrece
   la pantalla de Presupuestos —, comparando sin tildes, en mayúsculas y
   sin espacios alrededor de '/':
   a. nombre igual ('SALUD' → Salud);
   b. todas las palabras de uno están en el otro ('TRANSPORTE' →
      Transporte / Auto, 'COMPRA SUPERMERCADO 02/05' → Supermercado),
      si eso da una sola categoría;
   c. parecido de texto >= UMBRAL_SIMILITUD (difflib: 'COMIDA Y BEBIDA' →
      Comidas y Bebidas), si el mejor es uno solo.

Filas que van al MISMO presupuesto se SUMAN en una sola fila (no estaba en
el pedido; si no, la regla de idempotencia salteaba la segunda y se perdía
su monto): en un mes, la misma categoría para variables (ej. 'INVERSIONES'
e 'INVERSIONES MES PASADO') o el mismo concepto y moneda para fijos (ej.
dos boletas de GAS en 05/2024). La app además admite un solo variable por
categoría y mes (PresupuestosService). El reporte lista cada suma.

Idempotencia: un presupuesto se saltea si en la base ya hay uno igual —
fijo: mismo concepto + mes + año + moneda; variable: misma categoría + mes
+ año (sin mirar la moneda: es la regla de la app, un variable por
categoría y mes) —, así que se puede correr de nuevo sin duplicar.

TODO (notas): el pedido incluye la nota "MIGRADO DESDE EXCEL — TABLA
EGRESOS, FILA N", pero `presupuestos` no tiene columna `notas` (se descartó
en la reestructuración, decisión del usuario). No se guarda: las líneas de
origen de cada presupuesto solo salen en el reporte. Si hace falta, agregar
la columna en db/schema_migrations.py y escribirla acá.

Se inserta con SQL directo, en UNA transacción, por el mismo motivo que
migrar_ingresos.py. Nunca corre contra data/deltabalance.db.

Uso:
    # 1) Dry-run (no escribe nada en la base real, solo reporta):
    python migration/migrar_egresos.py

    # 2) Aplicar de verdad (hace backup antes):
    python migration/migrar_egresos.py --confirmar

    # Opcional: otro CSV u otra DB
    python migration/migrar_egresos.py --csv ~/Descargas/egresos.csv --db-path /tmp/prueba.db --confirmar

Este script NO lo ejecuta Claude Code (CLAUDE.md §0.1) — lo corre el
usuario a mano.
"""

from __future__ import annotations

import argparse
import difflib
import re
import sqlite3
import sys
from collections import Counter
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from migration.migrar_deudas import _clave, _numero
from migration.migrar_ingresos import (
    COL_CATEGORIA,
    COL_CONCEPTO,
    COL_FECHA,
    COL_MONEDA,
    COL_PRECIO,
    DB_PATH,
    RAIZ,
    _codigo_moneda,
    _concepto,
    _ejecutar,
    _mes_anio,
    _monedas,
    _mostrar_ruta,
)

CSV_DEFAULT = RAIZ / "migration" / "sources" / "REGISTRO PRINCIPAL - TABLA EGRESOS (2).csv"

CONCEPTOS_ENCABEZADO = {"CONCEPTO", "EGRESOS"}
# Reintegros / acreditaciones de promociones: no son un presupuesto (se saltean).
PREFIJOS_SALTEAR = (
    "REINTEGRO PROMO MODO",
    "REINTEGRO PROMO MOD",
    "ACREDITACION PROMO MODO",
    "ACREDITACIÓN PROMO MODO",
    "REINTEGRO CR INTERBANC",
)
# CATEGORIA del Excel, comparada con _clave() (sin tildes: 'CANCELACIÓN' → 'CANCELACION').
CATEGORIA_FIJOS = "EGRESOS FIJOS"
CATEGORIA_VARIABLES = "EGRESOS VARIABLES"
CATEGORIA_CANCELACION = "CANCELACION"

# Concepto del Excel → nombre de la categoría (subcategoria). Ver docstring, punto 1.
MAPA_VARIABLE = {
    "INVERSIONES":           "INVERSIONES",
    "AHORRO":                "AHORRO/INVERSIÓN",
    "TRANSPORTE SUBE":       "TRANSPORTE / AUTO",
    "COMBUSTIBLE":           "TRANSPORTE / AUTO",
    "TARJETA":               "SERVICIOS",
    "HONORARIOS":            "SERVICIOS",
    "REINTEGRO PROMOCION":   "REINTEGRO PROMOCION",
    "MOTO SAVINGS":          "AHORRO/INVERSIÓN",
    "VACACIONES SAVING":     "AHORRO/INVERSIÓN",
    "VACACIONES SAVINGS":    "AHORRO/INVERSIÓN",
    "CIRUGIA SAVINGS":       "AHORRO/INVERSIÓN",
    "ALIMENTO MAR SAVING":   "AHORRO/INVERSIÓN",
    "AHORRO AGUINALDO":      "AHORRO/INVERSIÓN",
    "AHORRO REGALO NOE":     "AHORRO/INVERSIÓN",
    # Si no está en el mapa → búsqueda por nombre similar; si tampoco → fallback a fijo con concepto
}
# Búsqueda por nombre similar (ver docstring, punto 2).
UMBRAL_SIMILITUD = 0.85
PALABRAS_IGNORADAS = {"Y", "DE", "DEL", "LA", "EL", "LOS", "LAS"}


# ============================================================
# CATEGORÍA DE UN EGRESO VARIABLE
# ============================================================

def _saltear_concepto(concepto: str) -> bool:
    return concepto.upper().strip().startswith(PREFIJOS_SALTEAR)


def _nombre(texto: str) -> str:
    """Para comparar nombres: _clave() (sin tildes, mayúsculas) y sin espacios alrededor de '/'."""
    return re.sub(r"\s*/\s*", "/", _clave(texto))


def _palabras(nombre: str) -> set[str]:
    return set(re.findall(r"[A-Z0-9]+", nombre)) - PALABRAS_IGNORADAS


class _Categorias:
    """Las categorías activas de la base, para resolver el concepto de un egreso variable (ver docstring)."""

    def __init__(self, conn: sqlite3.Connection):
        filas = conn.execute("SELECT id, subcategoria, tipo FROM categorias WHERE activa = 1;").fetchall()
        self._por_nombre = {_nombre(fila["subcategoria"]): fila for fila in filas}
        self._de_egreso = [(_nombre(fila["subcategoria"]), fila) for fila in filas if fila["tipo"] == "egreso"]
        self._mapa = {_nombre(clave): valor for clave, valor in MAPA_VARIABLE.items()}

    def resolver(self, concepto: str) -> tuple[Optional[sqlite3.Row], str]:
        """(categoría, cómo se encontró) — o (None, motivo) si va como fijo."""
        nombre = _nombre(concepto)

        # 1. MAPA_VARIABLE: concepto exacto o sus primeras palabras; gana la clave más larga.
        claves = [clave for clave in self._mapa if nombre == clave or nombre.startswith(clave + " ")]
        if claves:
            clave = max(claves, key=len)
            categoria = self._por_nombre.get(_nombre(self._mapa[clave]))
            if categoria is None:
                return None, f"MAPA '{clave}' → '{self._mapa[clave]}': esa categoría no existe o está desactivada"
            return categoria, f"MAPA '{clave}'"

        # 2a. Nombre igual.
        for nombre_categoria, categoria in self._de_egreso:
            if nombre_categoria == nombre:
                return categoria, "nombre igual"

        # 2b. Todas las palabras de uno están en el otro (una sola categoría).
        palabras = _palabras(nombre)
        contenidas = [
            categoria for nombre_categoria, categoria in self._de_egreso
            if palabras and _palabras(nombre_categoria)
            and (_palabras(nombre_categoria) <= palabras or palabras <= _palabras(nombre_categoria))
        ]
        if len(contenidas) == 1:
            return contenidas[0], "mismas palabras"

        # 2c. Parecido de texto (el mejor, si es uno solo).
        puntajes = sorted(
            ((difflib.SequenceMatcher(None, nombre, nombre_categoria).ratio(), categoria)
             for nombre_categoria, categoria in self._de_egreso),
            key=lambda par: par[0], reverse=True,
        )
        if puntajes and puntajes[0][0] >= UMBRAL_SIMILITUD and (len(puntajes) == 1 or puntajes[1][0] < puntajes[0][0]):
            return puntajes[0][1], f"parecido {puntajes[0][0]:.0%}"

        if len(contenidas) > 1:
            return None, "ambiguo: " + ", ".join(sorted(c["subcategoria"] for c in contenidas))
        return None, "sin categoría parecida"


# ============================================================
# IMPORTACIÓN
# ============================================================

def _importar(conn: sqlite3.Connection, filas: list[tuple[int, list[str]]]) -> dict:
    """Inserta en `presupuestos` (sin commit: decide el caller). Devuelve el reporte."""
    monedas = _monedas(conn)
    categorias = _Categorias(conn)
    fijos_existentes = {
        (fila["concepto"], fila["mes"], fila["anio"], fila["moneda_id"])
        for fila in conn.execute("SELECT concepto, mes, anio, moneda_id FROM presupuestos WHERE tipo = 'fijo';")
    }
    variables_existentes = {
        (fila["categoria_id"], fila["mes"], fila["anio"])
        for fila in conn.execute(
            "SELECT categoria_id, mes, anio FROM presupuestos WHERE tipo = 'variable' AND categoria_id IS NOT NULL;"
        )
    }
    salteadas: Counter = Counter()
    avisos: Counter = Counter()
    errores: list[str] = []
    # Concepto de un egreso variable → (categoría o None, cómo, cantidad de filas): para el reporte.
    mapeo: dict[str, list] = {}

    # --- 1. Filas del CSV → presupuestos a crear (las que van al mismo se suman) ---
    # clave → {tipo, concepto, categoria_id, nombre, mes, anio, moneda_id, codigo, estimado, recurrente, lineas}
    grupos: dict[tuple, dict] = {}
    for numero_linea, celdas in filas:
        concepto = _concepto(celdas[COL_CONCEPTO])
        if not concepto:
            salteadas["sin CONCEPTO"] += 1
            continue
        if _clave(concepto) in CONCEPTOS_ENCABEZADO:
            salteadas["título / encabezado"] += 1
            continue
        # concepto ya viene sin espacios de más: 'REINTEGRO PROMO MODO  29/01' también se saltea.
        if _saltear_concepto(concepto):
            salteadas["reintegro / acreditación de promoción (PREFIJOS_SALTEAR)"] += 1
            continue
        categoria_excel = _clave(celdas[COL_CATEGORIA])
        if categoria_excel == CATEGORIA_CANCELACION:
            salteadas["CATEGORIA CANCELACIÓN"] += 1
            continue
        if categoria_excel not in (CATEGORIA_FIJOS, CATEGORIA_VARIABLES):
            salteadas[f"CATEGORIA desconocida ({categoria_excel or 'vacía'})"] += 1
            continue

        precio = _numero(celdas[COL_PRECIO])
        if precio is None or precio <= 0:
            salteadas["sin PRECIO positivo (estimado)"] += 1
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
        estimado = round(precio * 10 ** moneda["decimales"])
        if not estimado:
            errores.append(f"línea {numero_linea}: PRECIO {precio!r} redondea a 0 — salteada")
            continue

        categoria = None
        if categoria_excel == CATEGORIA_VARIABLES:
            if concepto not in mapeo:
                mapeo[concepto] = [*categorias.resolver(concepto), 0]
            mapeo[concepto][2] += 1
            categoria = mapeo[concepto][0]

        if categoria is not None:
            clave = ("variable", categoria["id"], mes, anio, moneda["id"])
            nuevo = {"tipo": "variable", "concepto": None, "categoria_id": categoria["id"], "nombre": categoria["subcategoria"]}
        else:
            clave = ("fijo", concepto, mes, anio, moneda["id"])
            nuevo = {"tipo": "fijo", "concepto": concepto, "categoria_id": None, "nombre": concepto}
        grupo = grupos.setdefault(clave, {
            **nuevo, "mes": mes, "anio": anio, "moneda_id": moneda["id"], "codigo": codigo,
            "estimado": 0, "recurrente": 0, "lineas": [],
        })
        grupo["estimado"] += estimado
        # Fijo del Excel → recurrente; un variable (aunque caiga como fijo por fallback), no.
        grupo["recurrente"] = max(grupo["recurrente"], 1 if categoria_excel == CATEGORIA_FIJOS else 0)
        grupo["lineas"].append(numero_linea)

    # --- 2. Presupuestos → base (salteando los que ya existen) ---
    insertados: Counter = Counter()
    sumados: list[str] = []
    for grupo in grupos.values():
        periodo = f"{grupo['anio']:04d}-{grupo['mes']:02d}"
        if grupo["tipo"] == "variable":
            if (grupo["categoria_id"], grupo["mes"], grupo["anio"]) in variables_existentes:
                salteadas["ya existe (variable de la misma categoría en el mes)"] += len(grupo["lineas"])
                continue
        elif (grupo["concepto"], grupo["mes"], grupo["anio"], grupo["moneda_id"]) in fijos_existentes:
            salteadas["ya existe (fijo con el mismo concepto, mes, año y moneda)"] += len(grupo["lineas"])
            continue

        conn.execute(
            """
            INSERT INTO presupuestos
                (tipo, concepto, categoria_id, monto_estimado_minor, moneda_id, mes, anio, es_recurrente)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?);
            """,
            (
                grupo["tipo"], grupo["concepto"], grupo["categoria_id"], grupo["estimado"],
                grupo["moneda_id"], grupo["mes"], grupo["anio"], grupo["recurrente"],
            ),
        )
        if grupo["tipo"] == "fijo":
            insertados["fijos recurrentes" if grupo["recurrente"] else "fijos no recurrentes (fallback de variables)"] += 1
        else:
            # Un variable por categoría y mes: el mismo en otra moneda, más abajo, se saltea.
            variables_existentes.add((grupo["categoria_id"], grupo["mes"], grupo["anio"]))
            insertados["variables"] += 1
        if len(grupo["lineas"]) > 1:
            sumados.append(
                f"{periodo} {grupo['tipo'].upper()} {grupo['nombre'].upper()}: líneas "
                f"{', '.join(map(str, grupo['lineas']))} → {grupo['estimado'] / 100:,.2f} {grupo['codigo']}"
            )

    return {
        "insertados": insertados, "salteadas": salteadas, "avisos": avisos, "errores": errores,
        "mapeo": mapeo, "sumados": sumados,
    }


def _reporte(reporte: dict, filas_csv: int) -> None:
    insertados = reporte["insertados"]
    print(f"\n[CSV] {filas_csv} línea(s)")
    print(f"[IMPORTADOS] {sum(insertados.values())} presupuesto(s)")
    for tipo, cantidad in sorted(insertados.items()):
        print(f"  ✅ {tipo}: {cantidad}")
    for motivo, cantidad in sorted(reporte["salteadas"].items()):
        print(f"  ⏭️  líneas salteadas — {motivo}: {cantidad}")
    for aviso, cantidad in sorted(reporte["avisos"].items()):
        print(f"  ⚠️  {aviso}: {cantidad}")
    for error in reporte["errores"]:
        print(f"  ❌ {error}")

    if reporte["mapeo"]:
        print("\n[EGRESOS VARIABLES → CATEGORÍA] (revisalo antes de --confirmar)")
        for concepto, (categoria, como, cantidad) in sorted(reporte["mapeo"].items()):
            destino = f"VARIABLE {categoria['subcategoria'].upper()}" if categoria is not None else "FIJO (fallback)"
            print(f"  {concepto:<45} → {destino:<32} [{como}] ×{cantidad}")

    if reporte["sumados"]:
        print("\n[SUMADOS] varias líneas del CSV en un mismo presupuesto:")
        for linea in reporte["sumados"]:
            print(f"  {linea}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Importa la TABLA EGRESOS del Excel (CSV) a presupuestos.")
    parser.add_argument("--csv", default=None, help=f"Ruta al CSV (default: {_mostrar_ruta(CSV_DEFAULT)}).")
    parser.add_argument("--db-path", default=None, help=f"Ruta a la DB (default: {_mostrar_ruta(DB_PATH)}).")
    parser.add_argument("--confirmar", action="store_true", help="Aplica los cambios. Sin esto, solo dry-run.")
    args = parser.parse_args()

    _ejecutar(
        "Migración de la TABLA EGRESOS",
        csv_path=Path(args.csv).expanduser() if args.csv else CSV_DEFAULT,
        db_path=Path(args.db_path) if args.db_path else DB_PATH,
        confirmar=args.confirmar,
        importar=_importar,
        reporte=_reporte,
        mensaje_final="Presupuestos importados.",
    )


if __name__ == "__main__":
    main()
