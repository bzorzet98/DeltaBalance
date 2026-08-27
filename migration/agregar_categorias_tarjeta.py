"""
DeltaBalance — migration/agregar_categorias_tarjeta.py

Migración one-off: agrega a una base YA EXISTENTE las tres categorías
especiales de routing de cargos extra de tarjeta (Tarea 3 de
docs/PROXIMOS_PASOS.md) que db/seed.sql ahora incluye para bases nuevas —
"TARJETA DE CRÉDITO · Impuesto tarjeta / Recargo tarjeta /
Ajuste-Reintegro tarjeta", ver services/categorias_service.py
CATEGORIAS_PROTEGIDAS y services/fees_service.py CATEGORIAS_CARGO_EXTRA.

db/seed.sql usa INSERT OR IGNORE, pero DatabaseManager.inicializar() solo
lo aplica si la DB es NUEVA (`if not ya_existia`, ver db/database.py) — una
base que ya existía antes de este cambio (como data/deltabalance.db) NUNCA
va a recibir estas tres filas solo por actualizar seed.sql. Este script
hace ese trabajo para una base existente, mismo patrón que
migration/migrar_categorias_simplificadas.py (pero mucho más simple: acá
no hay renombres ni fusiones, solo altas puras — nada que pudiera romper
una fila ya existente que las referencie).

Es seguro correrlo más de una vez: si alguna de las tres ya existe (por
nombre exacto, ver services/categorias_service.py CATEGORIAS_PROTEGIDAS),
la registra como "ya existe" y no hace nada con ella.

Este script NO se corre solo ni lo ejecuta Claude Code (CLAUDE.md §0.1) —
lo corre el usuario a mano. Por default es dry-run (solo imprime qué
haría, no escribe nada); hace falta --confirmar para aplicar. Antes de
escribir siempre hace un backup con DatabaseManager.hacer_backup().

Uso:
    # 1) Dry-run contra la DB real (no escribe nada, solo reporta):
    python migration/agregar_categorias_tarjeta.py

    # 2) Aplicar de verdad contra la DB real (hace backup antes solo):
    python migration/agregar_categorias_tarjeta.py --confirmar
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db.database import DatabaseManager
from services.categorias_service import CategoriasError, CategoriasService

# Mismas tres filas que db/seed.sql — mantener sincronizado a mano si
# alguna vez cambia el nombre/tipo de alguna (lo cual además rompería el
# routing de ui/screens/compras_cuotas.py, ver CATEGORIAS_PROTEGIDAS).
CATEGORIAS_NUEVAS: list[tuple[str, str, str]] = [
    ("TARJETA DE CRÉDITO", "Impuesto tarjeta", "egreso"),
    ("TARJETA DE CRÉDITO", "Recargo tarjeta", "egreso"),
    ("TARJETA DE CRÉDITO", "Ajuste/Reintegro tarjeta", "egreso"),
]


def _buscar_id(db: DatabaseManager, principal: str, sub: str) -> int | None:
    """Busca por nombre SIN filtrar por activa — si ya existe (incluso
    desactivada por otra vía), no hay que volver a crearla."""
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
    print(f"=== Agregar categorías especiales de tarjeta — {modo} ===")
    print(f"DB: {db.db_path}\n")

    if args.confirmar:
        db.hacer_backup()

    for principal, sub, tipo in CATEGORIAS_NUEVAS:
        cat_id = _buscar_id(db, principal, sub)
        if cat_id is not None:
            print(f"⏭️  '{principal} · {sub}' ya existe (id={cat_id}) — sin acción.")
            continue

        print(f"➕ '{principal} · {sub}' (tipo={tipo})")
        if args.confirmar:
            try:
                resultado = svc.create_category(principal, sub, tipo)
                print(f"   ✅ Creada con id={resultado.categoria_id}.")
            except CategoriasError as err:
                print(f"   ❌ Falló: {err}")

    print("\n=== Fin ===")
    if not args.confirmar:
        print("Nada se escribió — corré de nuevo con --confirmar para aplicar.")

    db.desconectar()


if __name__ == "__main__":
    main()
