"""
DeltaBalance — migration/reestructurar_categorias.py

Migración one-off: lleva el catálogo de categorías de una base existente al
catálogo reestructurado (CATEGORIAS_FINALES, el mismo de db/seed.sql — ver
docs/DATA_MODEL_DECISIONS.md sección 30): todo en MAYÚSCULAS, los egresos
bajo una sola categoría principal EGRESOS, y algunas categorías fusionadas.
db/seed.sql usa INSERT OR IGNORE y solo alcanza a bases NUEVAS: este script
hace el trabajo sobre una base ya sembrada (la de la app).

Cada categoría de MAPA_MIGRACION que exista en la base va a su categoría
destino:

1. RENOMBRE (mismo id): si el destino todavía no existe, la categoría
   origen pasa a tener el nombre y el tipo del destino. Ninguna fila que la
   usa se toca — solo se sube a Supabase esa categoría. Si varias van al
   mismo destino (ej. Sueldo y BECA → SUELDO / BECA), se renombra la que
   más filas usan y las demás se FUSIONAN en ella.
2. FUSIÓN: si el destino ya existe (o lo ocupó un renombre), todas las filas
   de cada tabla con una FK a categorias (transacciones, compras_cuotas,
   presupuestos, gastos_compartidos — salen de PRAGMA foreign_key_list, no
   de una lista a mano) pasan a apuntar al destino, y la categoría origen se
   DESACTIVA (activa = 0, nunca DELETE: el historial se conserva).
3. CREACIÓN: cada categoría de CATEGORIAS_FINALES que después de eso no
   existe se crea (UUID nuevo). Una que existe inactiva se reactiva y una
   con otro tipo se corrige.

Un destino ya existente con otro tipo, o un origen cuyo tipo cambia (ej.
REINTEGRO PROMOCION: egreso → ingreso), se informa con cuántas
transacciones de cada tipo de movimiento tienen esa categoría: el tipo de
movimiento de las transacciones no se toca.

Las categorías que no están en MAPA_MIGRACION ni en CATEGORIAS_FINALES (ej.
las creadas a mano) quedan como están y se listan al final.

Nombres que usa el código: CATEGORIAS_PROTEGIDAS (services/
categorias_service.py), CATEGORIAS_CARGO_EXTRA (services/fees_service.py),
el routing del Registro y la categoría de ahorro reconocen las categorías
por nombre SIN distinguir mayúsculas (utils/categorias.py
clave_categoria()), con los nombres de CATEGORIAS_FINALES — funcionan antes
y después de correr este script.

Sincronización: todo lo que este script escribe pasa por los triggers de
sync — queda pendiente de subir en la próxima sincronización.
SINCRONIZÁ ANTES de correrlo (una fusión reescribe gastos compartidos: si
el otro miembro cambió uno y esta base todavía no lo bajó, la versión de
acá lo pisaría) y corrélo en la base de CADA miembro del hogar: un gasto
compartido lleva la categoría por nombre, y una base con los nombres viejos
no reconoce los nuevos (se crearía una categoría inactiva con ese nombre).

Este script NO lo ejecuta Claude Code (CLAUDE.md §0.1) — lo corre el
usuario a mano. Por default es dry-run (solo informa, no escribe nada);
hace falta --confirmar para aplicar. Con --confirmar: se niega si la app
tiene la base abierta, hace un backup (DatabaseManager.hacer_backup(),
después de pasar el -wal al archivo) y aplica TODO en una sola transacción
— si algo falla, no queda nada a medias. Correrlo de nuevo no rompe nada:
lo ya migrado aparece como "sin acción".

Sin --db-path usa la base de la app (db/database.py DB_PATH, hoy
data/deltabalanceBZ.db).

Uso:
    # 1) Dry-run contra la DB real (no escribe nada, solo informa):
    python migration/reestructurar_categorias.py

    # 2) Recomendado: probar contra una copia antes de tocar la real:
    cp data/deltabalanceBZ.db /tmp/prueba_categorias.db
    python migration/reestructurar_categorias.py --db-path /tmp/prueba_categorias.db --confirmar

    # 3) Aplicar de verdad contra la DB real (con la app cerrada):
    python migration/reestructurar_categorias.py --confirmar
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db.database import DB_PATH, DatabaseManager
from repositories._ids import nuevo_id

Clave = tuple[str, str]  # (categoria_principal, subcategoria)

# Catálogo final: (categoria_principal, subcategoria) → tipo. Mismo contenido
# que la sección CATEGORIAS de db/seed.sql.
CATEGORIAS_FINALES: dict[Clave, str] = {
    # EGRESOS
    ("EGRESOS", "VIVIENDA"):           "egreso",
    ("EGRESOS", "SERVICIOS BÁSICOS"):  "egreso",
    ("EGRESOS", "SEGUROS"):            "egreso",
    ("EGRESOS", "EDUCACIÓN"):          "egreso",
    ("EGRESOS", "TRANSPORTE / AUTO"):  "egreso",
    ("EGRESOS", "SUPERMERCADO"):       "egreso",
    ("EGRESOS", "ALIMENTOS"):          "egreso",
    ("EGRESOS", "GASTRONOMÍA"):        "egreso",
    ("EGRESOS", "SALUD"):              "egreso",
    ("EGRESOS", "DEPORTE"):            "egreso",
    ("EGRESOS", "INDUMENTARIA"):       "egreso",
    ("EGRESOS", "HOGAR"):              "egreso",
    ("EGRESOS", "MASCOTAS"):           "egreso",
    ("EGRESOS", "REGALOS"):            "egreso",
    ("EGRESOS", "OCIO"):               "egreso",
    ("EGRESOS", "VACACIONES"):         "egreso",
    ("EGRESOS", "EGRESO VARIABLE"):    "egreso",  # transitorio

    # INGRESOS
    ("INGRESOS", "SUELDO / BECA"):         "ingreso",
    ("INGRESOS", "INGRESO VARIABLE"):      "ingreso",
    ("INGRESOS", "REINTEGRO"):             "ingreso",
    ("INGRESOS", "REINTEGRO PROMOCIÓN"):   "ingreso",
    ("INGRESOS", "RENDIMIENTOS"):          "ingreso",
    ("INGRESOS", "COBRO DEUDA"):           "ingreso",

    # MOVIMIENTO CAPITAL
    ("MOVIMIENTO CAPITAL", "AUTOTRANSFERENCIA"): "movimiento",
    ("MOVIMIENTO CAPITAL", "AHORRO/INVERSIÓN"):  "movimiento",
    ("MOVIMIENTO CAPITAL", "INVERSIONES"):       "movimiento",
    ("MOVIMIENTO CAPITAL", "CAMBIO MONEDA"):     "movimiento",
    ("MOVIMIENTO CAPITAL", "DEUDA"):             "movimiento",

    # TARJETA DE CRÉDITO
    ("TARJETA DE CRÉDITO", "IMPUESTO TARJETA"):          "egreso",
    ("TARJETA DE CRÉDITO", "AJUSTE/REINTEGRO TARJETA"):  "egreso",
    ("TARJETA DE CRÉDITO", "PAGO TARJETA"):              "egreso",
    ("TARJETA DE CRÉDITO", "RECARGO TARJETA"):           "egreso",
}

# Categoría existente → categoría final. Nombres EXACTOS (con mayúsculas y
# acentos tal como están en la base): una que no está en la base se saltea.
MAPA_MIGRACION: dict[Clave, Clave] = {
    # Normalizar mayúsculas y categoría principal
    ("EGRESOS FIJOS",    "Alquiler / Vivienda"):   ("EGRESOS", "VIVIENDA"),
    ("EGRESOS FIJOS",    "Educacion"):              ("EGRESOS", "EDUCACIÓN"),
    ("EGRESOS FIJOS",    "Impuestos"):              ("EGRESOS", "SERVICIOS BÁSICOS"),
    ("EGRESOS FIJOS",    "Seguros"):                ("EGRESOS", "SEGUROS"),
    ("EGRESOS FIJOS",    "Servicios"):              ("EGRESOS", "SERVICIOS BÁSICOS"),

    ("EGRESOS VARIABLES", "ALIMENTOS"):             ("EGRESOS", "ALIMENTOS"),
    ("EGRESOS VARIABLES", "Bienestar y Deporte"):   ("EGRESOS", "DEPORTE"),
    ("EGRESOS VARIABLES", "Comidas y Bebidas"):     ("EGRESOS", "GASTRONOMÍA"),
    ("EGRESOS VARIABLES", "EGRESO VARIABLE"):       ("EGRESOS", "EGRESO VARIABLE"),
    ("EGRESOS VARIABLES", "GASTRONOMÍA"):           ("EGRESOS", "GASTRONOMÍA"),
    ("EGRESOS VARIABLES", "Hogar"):                 ("EGRESOS", "HOGAR"),
    ("EGRESOS VARIABLES", "MASCOTAS"):              ("EGRESOS", "MASCOTAS"),
    ("EGRESOS VARIABLES", "Ocio"):                  ("EGRESOS", "OCIO"),
    ("EGRESOS VARIABLES", "REGALOS"):               ("EGRESOS", "REGALOS"),
    ("EGRESOS VARIABLES", "REINTEGRO PROMOCION"):   ("INGRESOS", "REINTEGRO PROMOCIÓN"),
    ("EGRESOS VARIABLES", "Regalos y Mascotas"):    ("EGRESOS", "REGALOS"),
    ("EGRESOS VARIABLES", "Ropa"):                  ("EGRESOS", "INDUMENTARIA"),
    ("EGRESOS VARIABLES", "Salud"):                 ("EGRESOS", "SALUD"),
    ("EGRESOS VARIABLES", "Supermercado"):          ("EGRESOS", "SUPERMERCADO"),
    ("EGRESOS VARIABLES", "Transporte / Auto"):     ("EGRESOS", "TRANSPORTE / AUTO"),
    ("EGRESOS VARIABLES", "VACACIONES"):            ("EGRESOS", "VACACIONES"),

    ("INGRESOS", "BECA"):                           ("INGRESOS", "SUELDO / BECA"),
    ("INGRESOS", "Cobro Deuda"):                    ("INGRESOS", "COBRO DEUDA"),
    ("INGRESOS", "INGRESO VARIABLE"):               ("INGRESOS", "INGRESO VARIABLE"),
    ("INGRESOS", "Reintegro"):                      ("INGRESOS", "REINTEGRO"),
    ("INGRESOS", "Sueldo"):                         ("INGRESOS", "SUELDO / BECA"),

    ("MOVIMIENTO CAPITAL", "Ahorro/Inversión"):     ("MOVIMIENTO CAPITAL", "AHORRO/INVERSIÓN"),
    ("MOVIMIENTO CAPITAL", "Autotransferencia"):    ("MOVIMIENTO CAPITAL", "AUTOTRANSFERENCIA"),
    ("MOVIMIENTO CAPITAL", "Cambio Moneda"):        ("MOVIMIENTO CAPITAL", "CAMBIO MONEDA"),
    ("MOVIMIENTO CAPITAL", "Deuda"):                ("MOVIMIENTO CAPITAL", "DEUDA"),
    ("MOVIMIENTO CAPITAL", "Inversiones"):          ("MOVIMIENTO CAPITAL", "INVERSIONES"),
    ("MOVIMIENTO CAPITAL", "Rendimientos"):         ("INGRESOS", "RENDIMIENTOS"),

    ("TARJETA DE CRÉDITO", "Ajuste/Reintegro tarjeta"): ("TARJETA DE CRÉDITO", "AJUSTE/REINTEGRO TARJETA"),
    ("TARJETA DE CRÉDITO", "Impuesto tarjeta"):          ("TARJETA DE CRÉDITO", "IMPUESTO TARJETA"),
    ("TARJETA DE CRÉDITO", "Pago Tarjeta"):              ("TARJETA DE CRÉDITO", "PAGO TARJETA"),
    ("TARJETA DE CRÉDITO", "Recargo tarjeta"):           ("TARJETA DE CRÉDITO", "RECARGO TARJETA"),
}


class MigracionError(Exception):
    """La migración no se puede correr (base abierta, mapa inconsistente)."""


def _texto(clave: Clave) -> str:
    return f"{clave[0]} / {clave[1]}"


# =============================================================
# PLAN (solo lectura: es lo que imprime el dry-run y lo que aplica --confirmar)
# =============================================================

@dataclass
class Renombre:
    categoria_id: str
    origen: Clave
    destino: Clave
    tipo_origen: str
    tipo_destino: str
    usos: dict[str, int]  # tabla → filas que la usan (no se tocan: mismo id)


@dataclass
class Fusion:
    origen_id: str
    origen: Clave
    destino: Clave
    tipo_origen: str
    tipo_destino: str
    usos: dict[str, int]  # tabla → filas que se redirigen al destino


@dataclass
class Plan:
    renombres: list[Renombre] = field(default_factory=list)
    fusiones: list[Fusion] = field(default_factory=list)
    creadas: list[Clave] = field(default_factory=list)
    reactivadas: list[tuple[str, Clave]] = field(default_factory=list)
    tipos_corregidos: list[tuple[str, Clave, str, str]] = field(default_factory=list)  # (id, clave, de, a)
    sin_accion: list[str] = field(default_factory=list)
    no_encontradas: list[Clave] = field(default_factory=list)
    fuera_del_catalogo: list[Clave] = field(default_factory=list)
    # destino → id de la categoría que lo representa (existente o renombrada); None = se crea.
    id_destino: dict[Clave, Optional[str]] = field(default_factory=dict)


def _validar_mapa() -> None:
    destinos_fuera = sorted({d for d in MAPA_MIGRACION.values() if d not in CATEGORIAS_FINALES})
    if destinos_fuera:
        raise MigracionError(f"Destinos que no están en CATEGORIAS_FINALES: {', '.join(map(_texto, destinos_fuera))}")


def _tablas_con_categoria(db: DatabaseManager) -> list[tuple[str, str]]:
    """(tabla, columna) de cada FK a categorias(id), de todas las tablas de la base (PRAGMA foreign_key_list)."""
    tablas = [f["name"] for f in db.fetchall("SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name;")]
    return [
        (tabla, fk["from"])
        for tabla in tablas
        for fk in db.fetchall(f"PRAGMA foreign_key_list({tabla});")
        if fk["table"] == "categorias"
    ]


def _usos(db: DatabaseManager, tablas: list[tuple[str, str]], categoria_id: str) -> dict[str, int]:
    """tabla → cuántas filas apuntan a esa categoría (solo las que tienen alguna)."""
    usos: dict[str, int] = {}
    for tabla, columna in tablas:
        n = db.fetchone(f"SELECT COUNT(*) AS n FROM {tabla} WHERE {columna} = ?;", (categoria_id,))["n"]
        if n:
            usos[tabla] = n
    return usos


def armar_plan(db: DatabaseManager) -> Plan:
    """Qué haría la migración sobre esta base, sin escribir nada (ver docstring del módulo)."""
    _validar_mapa()
    tablas = _tablas_con_categoria(db)
    existentes: dict[Clave, dict] = {
        (c["categoria_principal"], c["subcategoria"]): dict(c) for c in db.fetchall("SELECT * FROM categorias;")
    }
    plan = Plan()

    # Orígenes presentes, agrupados por destino (en el orden de MAPA_MIGRACION).
    por_destino: dict[Clave, list[Clave]] = {}
    for origen, destino in MAPA_MIGRACION.items():
        if origen == destino:
            continue  # ya tiene el nombre final: lo cubre la parte de CATEGORIAS_FINALES
        if origen not in existentes:
            if destino in existentes:  # una corrida anterior ya la renombró
                plan.sin_accion.append(f"{_texto(origen)} → {_texto(destino)}: ya migrada")
            else:
                plan.no_encontradas.append(origen)
            continue
        por_destino.setdefault(destino, []).append(origen)

    for destino, origenes in por_destino.items():
        tipo_destino = CATEGORIAS_FINALES[destino]
        usos = {o: _usos(db, tablas, existentes[o]["id"]) for o in origenes}
        if destino in existentes:
            plan.id_destino[destino] = existentes[destino]["id"]
            a_fusionar = origenes
        else:
            # Se renombra la que más filas usan: menos filas que redirigir (y que subir).
            base = max(origenes, key=lambda o: sum(usos[o].values()))
            fila = existentes[base]
            plan.renombres.append(Renombre(
                categoria_id=fila["id"], origen=base, destino=destino, tipo_origen=fila["tipo"],
                tipo_destino=tipo_destino, usos=usos[base],
            ))
            plan.id_destino[destino] = fila["id"]
            a_fusionar = [o for o in origenes if o != base]
        for origen in a_fusionar:
            fila = existentes[origen]
            if not fila["activa"] and not usos[origen]:
                plan.sin_accion.append(f"{_texto(origen)} → {_texto(destino)}: ya fusionada (inactiva, sin filas)")
                continue
            plan.fusiones.append(Fusion(
                origen_id=fila["id"], origen=origen, destino=destino, tipo_origen=fila["tipo"],
                tipo_destino=tipo_destino, usos=usos[origen],
            ))

    # Catálogo final: lo que falta se crea; lo que existe inactivo o con otro tipo se corrige.
    renombrados = {r.destino for r in plan.renombres}
    for clave, tipo in CATEGORIAS_FINALES.items():
        if clave in renombrados:
            continue
        fila = existentes.get(clave)
        if fila is None:
            plan.creadas.append(clave)
            plan.id_destino.setdefault(clave, None)
            continue
        if not fila["activa"]:
            plan.reactivadas.append((fila["id"], clave))
        if fila["tipo"] != tipo:
            plan.tipos_corregidos.append((fila["id"], clave, fila["tipo"], tipo))
        if fila["activa"] and fila["tipo"] == tipo:
            plan.sin_accion.append(f"{_texto(clave)}: ya está")

    origenes_del_mapa = set(MAPA_MIGRACION)
    plan.fuera_del_catalogo = sorted(
        clave for clave, fila in existentes.items()
        if fila["activa"] and clave not in origenes_del_mapa and clave not in CATEGORIAS_FINALES
    )
    return plan


def _transacciones_por_tipo(db: DatabaseManager, categoria_id: str) -> str:
    """'egreso: 240, ingreso: 3' — tipo_movimiento de las transacciones con esa categoría."""
    filas = db.fetchall(
        "SELECT tipo_movimiento, COUNT(*) AS n FROM transacciones WHERE categoria_id = ? GROUP BY tipo_movimiento;",
        (categoria_id,),
    )
    return ", ".join(f"{f['tipo_movimiento']}: {f['n']}" for f in filas) or "ninguna"


def _texto_usos(usos: dict[str, int]) -> str:
    return ", ".join(f"{n} {tabla}" for tabla, n in usos.items()) or "ninguna fila"


def imprimir_plan(db: DatabaseManager, plan: Plan) -> None:
    print("[RENOMBRADAS] (mismo id: las filas que la usan no se tocan)")
    for r in plan.renombres:
        print(f"  {_texto(r.origen)} → {_texto(r.destino)} ({_texto_usos(r.usos)})")
    if not plan.renombres:
        print("  —")

    print("\n[MIGRADAS] (fusión: las filas pasan al destino y la origen se desactiva)")
    for f in plan.fusiones:
        print(f"  {_texto(f.origen)} → {_texto(f.destino)} ({_texto_usos(f.usos)} actualizadas)")
    if not plan.fusiones:
        print("  —")

    print("\n[CREADAS]")
    for clave in plan.creadas:
        print(f"  {_texto(clave)}")
    if not plan.creadas:
        print("  —")

    print("\n[DESACTIVADAS]")
    for f in plan.fusiones:
        print(f"  {_texto(f.origen)}")
    if not plan.fusiones:
        print("  —")

    if plan.reactivadas:
        print("\n[REACTIVADAS] (del catálogo final, estaban inactivas)")
        for _, clave in plan.reactivadas:
            print(f"  {_texto(clave)}")

    cambios_de_tipo = (
        [(r.categoria_id, r.origen, r.destino, r.tipo_origen, r.tipo_destino) for r in plan.renombres
         if r.tipo_origen != r.tipo_destino]
        + [(f.origen_id, f.origen, f.destino, f.tipo_origen, f.tipo_destino) for f in plan.fusiones
           if f.tipo_origen != f.tipo_destino]
        + [(i, c, c, de, a) for i, c, de, a in plan.tipos_corregidos]
    )
    if cambios_de_tipo:
        print("\n[⚠️ CAMBIA EL TIPO DE CATEGORÍA] (el tipo de movimiento de las transacciones NO se toca)")
        for categoria_id, origen, destino, de, a in cambios_de_tipo:
            print(
                f"  {_texto(origen)} → {_texto(destino)}: {de} → {a} "
                f"(transacciones por tipo de movimiento: {_transacciones_por_tipo(db, categoria_id)})"
            )

    if plan.no_encontradas:
        print("\n[NO ESTÁN EN ESTA BASE] (se saltean)")
        for clave in plan.no_encontradas:
            print(f"  {_texto(clave)}")

    if plan.fuera_del_catalogo:
        print("\n[FUERA DEL CATÁLOGO] (activas, ni en el mapa ni en el catálogo final: quedan como están)")
        for clave in plan.fuera_del_catalogo:
            print(f"  {_texto(clave)}")

    if plan.sin_accion:
        print(f"\n[SIN ACCIÓN] {len(plan.sin_accion)} (ya migradas o ya con el nombre final)")


# =============================================================
# APLICAR (una sola transacción)
# =============================================================

def aplicar_plan(db: DatabaseManager, plan: Plan) -> None:
    """Escribe el plan en UNA transacción: si algo falla, rollback completo (la excepción sube)."""
    tablas = _tablas_con_categoria(db)
    with db.transaction() as conn:
        for r in plan.renombres:
            conn.execute(
                "UPDATE categorias SET categoria_principal = ?, subcategoria = ?, tipo = ?, activa = 1 WHERE id = ?;",
                (r.destino[0], r.destino[1], r.tipo_destino, r.categoria_id),
            )
        for clave in plan.creadas:
            plan.id_destino[clave] = nuevo_id()
            conn.execute(
                "INSERT INTO categorias (id, categoria_principal, subcategoria, tipo) VALUES (?, ?, ?, ?);",
                (plan.id_destino[clave], clave[0], clave[1], CATEGORIAS_FINALES[clave]),
            )
        for categoria_id, _ in plan.reactivadas:
            conn.execute("UPDATE categorias SET activa = 1 WHERE id = ?;", (categoria_id,))
        for categoria_id, _, _, tipo in plan.tipos_corregidos:
            conn.execute("UPDATE categorias SET tipo = ? WHERE id = ?;", (tipo, categoria_id))
        for f in plan.fusiones:
            destino_id = plan.id_destino[f.destino]
            for tabla, columna in tablas:
                conn.execute(f"UPDATE {tabla} SET {columna} = ? WHERE {columna} = ?;", (destino_id, f.origen_id))
            conn.execute("UPDATE categorias SET activa = 0 WHERE id = ?;", (f.origen_id,))


def _comprobar_base_libre(db_path: Path) -> None:
    """Si después de abrir y cerrar la base siguen el -wal/-shm, otra conexión (la app) la tiene abierta."""
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
    finally:
        conn.close()
    restos = [p for p in (Path(f"{db_path}-wal"), Path(f"{db_path}-shm")) if p.exists()]
    if restos:
        raise MigracionError(
            f"La base parece estar abierta por otro programa (siguen {', '.join(p.name for p in restos)}). "
            "Cerrá la app y volvé a correr."
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db-path", default=None, help=f"Ruta a la DB (default: {DB_PATH}).")
    parser.add_argument("--confirmar", action="store_true", help="Aplica los cambios. Sin esto, solo dry-run.")
    args = parser.parse_args()

    db_path = Path(args.db_path) if args.db_path else DB_PATH
    if not db_path.exists():
        sys.exit(f"No existe la base {db_path}.")
    if args.confirmar:
        try:
            _comprobar_base_libre(db_path)
        except MigracionError as err:
            sys.exit(str(err))

    db = DatabaseManager(db_path=db_path)
    db.inicializar()  # idempotente: schema + migraciones; seed solo si la DB es nueva
    modo = "APLICANDO CAMBIOS" if args.confirmar else "DRY-RUN (nada se escribe)"
    print(f"=== Reestructuración de categorías — {modo} ===")
    print(f"DB: {db.db_path}\n")

    try:
        plan = armar_plan(db)
    except MigracionError as err:
        db.desconectar()
        sys.exit(str(err))
    imprimir_plan(db, plan)

    if args.confirmar:
        # El backup copia el archivo: antes, lo del -wal pasa al archivo.
        db.conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
        db.hacer_backup()
        aplicar_plan(db, plan)
        print("\n✅ Aplicado. Sincronizá para subir los cambios, y corré este script también en la base del otro miembro del hogar.")
    else:
        print("\nNada se escribió — corré de nuevo con --confirmar para aplicar (con la app cerrada).")

    db.desconectar()


if __name__ == "__main__":
    main()
