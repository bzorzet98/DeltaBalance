"""
DeltaBalance — migration/agregar_categoria_ahorro_inversion.py

Migración one-off: agrega a una base YA EXISTENTE la categoría especial de
routing "MOVIMIENTO CAPITAL · Ahorro/Inversión" (Tarea 1b de
docs/PROXIMOS_PASOS.md) que db/seed.sql ahora incluye para bases nuevas —
ver services/categorias_service.py CATEGORIAS_PROTEGIDAS y
ui/components/registro_transacciones.py.

db/seed.sql usa INSERT OR IGNORE, pero DatabaseManager.inicializar() solo
lo aplica si la DB es NUEVA (`if not ya_existia`, ver db/database.py) — una
base que ya existía antes de este cambio (como data/deltabalance.db) NUNCA
va a recibir esta fila solo por actualizar seed.sql. Este script hace ese
trabajo para una base existente, mismo patrón exacto que
migration/agregar_categorias_tarjeta.py (pero para una sola categoría en
vez de tres).

Es seguro correrlo más de una vez: si la categoría ya existe (por nombre
exacto), la registra como "ya existe" y no hace nada con ella.

Este script NO se corre solo ni lo ejecuta Claude Code (CLAUDE.md §0.1) —
lo corre el usuario a mano. Por default es dry-run (solo imprime qué
haría, no escribe nada); hace falta --confirmar para aplicar. Antes de
escribir siempre hace un backup con DatabaseManager.hacer_backup().

Uso:
    # 1) Dry-run contra la DB real (no escribe nada, solo reporta):
    python migration/agregar_categoria_ahorro_inversion.py

    # 2) Aplicar de verdad contra la DB real (hace backup antes solo):
    python migration/agregar_categoria_ahorro_inversion.py --confirmar
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db.database import DatabaseManager
from services.categorias_service import CategoriasError, CategoriasService

# Misma fila que db/seed.sql — mantener sincronizado a mano si alguna vez
# cambia el nombre/tipo (lo cual además rompería el routing de
# ui/components/registro_transacciones.py, ver CATEGORIAS_PROTEGIDAS).
CATEGORIA_PRINCIPAL = "MOVIMIENTO CAPITAL"
SUBCATEGORIA = "Ahorro/Inversión"
TIPO = "movimiento"


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
    print(f"=== Agregar categoría 'Ahorro/Inversión' — {modo} ===")
    print(f"DB: {db.db_path}\n")

    if args.confirmar:
        db.hacer_backup()

    cat_id = _buscar_id(db, CATEGORIA_PRINCIPAL, SUBCATEGORIA)
    if cat_id is not None:
        print(f"⏭️  '{CATEGORIA_PRINCIPAL} · {SUBCATEGORIA}' ya existe (id={cat_id}) — sin acción.")
    else:
        print(f"➕ '{CATEGORIA_PRINCIPAL} · {SUBCATEGORIA}' (tipo={TIPO})")
        if args.confirmar:
            try:
                resultado = svc.create_category(CATEGORIA_PRINCIPAL, SUBCATEGORIA, TIPO)
                print(f"   ✅ Creada con id={resultado.categoria_id}.")
            except CategoriasError as err:
                print(f"   ❌ Falló: {err}")

    print("\n=== Fin ===")
    if not args.confirmar:
        print("Nada se escribió — corré de nuevo con --confirmar para aplicar.")

    db.desconectar()


if __name__ == "__main__":
    main()
