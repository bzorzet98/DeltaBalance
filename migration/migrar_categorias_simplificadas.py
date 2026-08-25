"""
DeltaBalance — migration/migrar_categorias_simplificadas.py

Migración one-off: lleva una base ya sembrada con las 27 categorías
originales a la lista simplificada de 21 que ahora tiene db/seed.sql
(propuesta y aprobada por el usuario el 2026-08-25). db/seed.sql usa
INSERT OR IGNORE, que solo alcanza a bases NUEVAS — nunca renombra ni
fusiona filas que una base ya sembrada trae con los nombres viejos. Este
script hace ese trabajo para una base existente (como data/deltabalance.db):

1. RENOMBRES: 3 categorías cambian de subcategoria in situ (mismo id, así
   que ninguna fila que las referencia necesita tocarse).
2. FUSIONES: 6 categorías viejas se retiran — antes de desactivarlas
   (CategoriasService.deactivate_category(), soft-delete, nunca DELETE
   físico) se redirigen todas las filas de transacciones/compras_cuotas/
   gastos_compartidos/presupuestos que las referencian hacia la categoría
   destino ya renombrada.

Las categorías protegidas (services/categorias_service.py
CATEGORIAS_PROTEGIDAS: INGRESOS · Sueldo, MOVIMIENTO CAPITAL ·
Autotransferencia) no participan de ningún renombre ni fusión acá.

Este script NO se corre solo ni lo ejecuta Claude Code (CLAUDE.md §0.1) —
lo corre el usuario a mano. Por default es dry-run (solo imprime qué haría,
no escribe nada); hace falta --confirmar para aplicar. Antes de escribir
siempre hace un backup con DatabaseManager.hacer_backup(). Es seguro
correrlo más de una vez (si una categoría vieja ya no existe o ya está
fusionada, la registra como "sin acción" en vez de fallar).

Uso:
    # 1) Dry-run contra la DB real (no escribe nada, solo reporta):
    python migration/migrar_categorias_simplificadas.py

    # 2) Recomendado: probar contra una copia antes de tocar la real:
    cp data/deltabalance.db /tmp/prueba_categorias.db
    python migration/migrar_categorias_simplificadas.py --db-path /tmp/prueba_categorias.db --confirmar

    # 3) Aplicar de verdad contra la DB real (hace backup antes solo):
    python migration/migrar_categorias_simplificadas.py --confirmar
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db.database import DatabaseManager
from services.categorias_service import CategoriasError, CategoriasService

# (categoria_principal, subcategoria_vieja, subcategoria_nueva) — mismo id,
# no requiere redirigir ninguna fila que la referencia.
RENOMBRES: list[tuple[str, str, str]] = [
    ("EGRESOS VARIABLES", "Hogar: Mantenimiento", "Hogar"),
    ("EGRESOS VARIABLES", "Ocio: Salidas", "Ocio"),
    ("EGRESOS VARIABLES", "Regalos", "Regalos y Mascotas"),
]

# (principal_origen, sub_origen, principal_destino, sub_destino). Corren
# DESPUÉS de RENOMBRES — los destinos ya deben existir con su nombre final.
FUSIONES: list[tuple[str, str, str, str]] = [
    ("INGRESOS", "Reintegro Promocion", "INGRESOS", "Reintegro"),
    ("EGRESOS VARIABLES", "Vivero y Jardin", "EGRESOS VARIABLES", "Hogar"),
    ("EGRESOS VARIABLES", "Ocio: Entretenimiento", "EGRESOS VARIABLES", "Ocio"),
    ("MOVIMIENTO CAPITAL", "Salud: Obra Social", "EGRESOS VARIABLES", "Salud"),
    ("EGRESOS VARIABLES", "Mascotas", "EGRESOS VARIABLES", "Regalos y Mascotas"),
    ("MOVIMIENTO CAPITAL", "Sinking Funds", "MOVIMIENTO CAPITAL", "Inversiones"),
]

# Tablas con categoria_id que se redirigen con un UPDATE simple — ninguna
# tiene una constraint que choque al fusionar (a diferencia de
# `presupuestos`, que se maneja aparte por su UNIQUE(categoria_id, mes, anio)).
TABLAS_CATEGORIA_ID_SIMPLE = ["transacciones", "compras_cuotas", "gastos_compartidos"]


def _buscar_id(db: DatabaseManager, principal: str, sub: str) -> Optional[int]:
    """Busca por nombre SIN filtrar por activa — necesitamos encontrar
    también categorías ya desactivadas por una corrida anterior de este
    mismo script."""
    fila = db.fetchone(
        "SELECT id FROM categorias WHERE categoria_principal = ? AND subcategoria = ?;",
        (principal, sub),
    )
    return fila["id"] if fila else None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-path", default=None, help="Ruta a la DB (default: data/deltabalance.db).")
    parser.add_argument("--confirmar", action="store_true", help="Aplica los cambios. Sin esto, solo dry-run.")
    args = parser.parse_args()

    db = DatabaseManager(db_path=Path(args.db_path)) if args.db_path else DatabaseManager()
    db.inicializar()  # idempotente: schema + migraciones de columna; seed solo si la DB es nueva
    svc = CategoriasService(db)

    modo = "APLICANDO CAMBIOS" if args.confirmar else "DRY-RUN (nada se escribe)"
    print(f"=== Migración de categorías simplificadas — {modo} ===")
    print(f"DB: {db.db_path}\n")

    if args.confirmar:
        db.hacer_backup()

    # --------------------------------------------------------------
    # 1) RENOMBRES
    # --------------------------------------------------------------
    print("--- Renombres ---")
    for principal, sub_vieja, sub_nueva in RENOMBRES:
        cat_id = _buscar_id(db, principal, sub_vieja)
        if cat_id is None:
            if _buscar_id(db, principal, sub_nueva) is not None:
                print(f"⏭️  '{principal} · {sub_vieja}' ya no existe, '{sub_nueva}' ya está — sin acción.")
            else:
                print(f"⚠️  '{principal} · {sub_vieja}' no existe en esta DB — se salta.")
            continue
        print(f"✏️  '{principal} · {sub_vieja}' → '{sub_nueva}'")
        if args.confirmar:
            try:
                svc.update_category(cat_id, subcategoria=sub_nueva)
            except CategoriasError as err:
                print(f"   ❌ Falló: {err}")

    # --------------------------------------------------------------
    # 2) FUSIONES
    # --------------------------------------------------------------
    print("\n--- Fusiones ---")
    for principal_o, sub_o, principal_d, sub_d in FUSIONES:
        origen_id = _buscar_id(db, principal_o, sub_o)
        destino_id = _buscar_id(db, principal_d, sub_d)

        if origen_id is None:
            print(f"⏭️  Origen '{principal_o} · {sub_o}' no existe — sin acción.")
            continue
        if destino_id is None:
            print(f"❌ Destino '{principal_d} · {sub_d}' no existe todavía — ¿corrieron los RENOMBRES? Se salta.")
            continue
        if origen_id == destino_id:
            print(f"⏭️  '{principal_o} · {sub_o}' y el destino ya son la misma categoría — sin acción.")
            continue

        print(f"🔀 '{principal_o} · {sub_o}' (id={origen_id}) → '{principal_d} · {sub_d}' (id={destino_id})")

        for tabla in TABLAS_CATEGORIA_ID_SIMPLE:
            fila = db.fetchone(f"SELECT COUNT(*) AS n FROM {tabla} WHERE categoria_id = ?;", (origen_id,))
            cantidad = fila["n"] if fila else 0
            if cantidad == 0:
                continue
            print(f"   → {cantidad} fila(s) en {tabla} redirigidas.")
            if args.confirmar:
                db.execute(
                    f"UPDATE {tabla} SET categoria_id = ? WHERE categoria_id = ?;",
                    (destino_id, origen_id),
                )

        # presupuestos: UNIQUE(categoria_id, mes, anio) — se maneja fila por
        # fila, nunca con un UPDATE masivo que podría violar la constraint
        # si origen y destino ya tenían presupuesto cargado para el mismo
        # mes/año.
        for p in db.fetchall("SELECT id, mes, anio FROM presupuestos WHERE categoria_id = ?;", (origen_id,)):
            conflicto = db.fetchone(
                "SELECT id FROM presupuestos WHERE categoria_id = ? AND mes = ? AND anio = ?;",
                (destino_id, p["mes"], p["anio"]),
            )
            if conflicto:
                print(
                    f"   ⚠️  presupuestos {p['mes']:02d}/{p['anio']} (id={p['id']}) ya tiene presupuesto en "
                    f"el destino — queda SIN redirigir, resolvelo a mano desde la UI."
                )
                continue
            print(f"   → presupuesto {p['mes']:02d}/{p['anio']} (id={p['id']}) redirigido.")
            if args.confirmar:
                db.execute("UPDATE presupuestos SET categoria_id = ? WHERE id = ?;", (destino_id, p["id"]))

        if args.confirmar:
            try:
                svc.deactivate_category(origen_id)
                print(f"   ✅ '{principal_o} · {sub_o}' desactivada.")
            except CategoriasError as err:
                print(f"   ❌ No se pudo desactivar: {err}")
        else:
            print(f"   (se desactivaría '{principal_o} · {sub_o}' al confirmar)")

    print("\n=== Fin ===")
    if not args.confirmar:
        print("Nada se escribió — corré de nuevo con --confirmar para aplicar.")

    db.desconectar()


if __name__ == "__main__":
    main()
