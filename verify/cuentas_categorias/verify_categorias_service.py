"""
verify/cuentas_categorias/verify_categorias_service.py

Verifica CategoriasService (services/categorias_service.py) — creado desde
cero en Fase 5 (flujo del botón "+" del dashboard) como exposición mínima
de solo lectura de CategoriasRepository para la UI. Cubre list_categories()
(sin filtro, filtrado por tipo, incluir_inactivas) y get_category() (existe
/ no existe).

Correlo con:
    python verify/cuentas_categorias/verify_categorias_service.py
"""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from verify._dummy_db import crear_dummy_db
from db.database import DatabaseManager
from services.categorias_service import CategoriasService


def main() -> None:
    casos_ok = 0
    casos_total = 0

    def caso(descripcion: str, esperado, obtenido) -> None:
        nonlocal casos_ok, casos_total
        casos_total += 1
        if esperado == obtenido:
            casos_ok += 1
            print(f"✅ {descripcion} — esperado: {esperado!r}, obtenido: {obtenido!r}")
        else:
            print(f"❌ {descripcion} — esperado: {esperado!r}, obtenido: {obtenido!r}")

    db_path = crear_dummy_db()
    print(f"Dummy DB creada en: {db_path}\n")

    manager = DatabaseManager(db_path=db_path)
    manager.inicializar()
    svc = CategoriasService(manager)

    print("--- list_categories() — sin filtro ---")
    todas = svc.list_categories()
    caso("list_categories() sin filtro devuelve categorías del seed", True, len(todas) > 0)
    caso("list_categories() sin filtro solo trae activas por default", True, all(c["activa"] == 1 for c in todas))

    print("\n--- list_categories() — filtrado por tipo ---")
    solo_ingreso = svc.list_categories(tipo="ingreso")
    caso("list_categories(tipo='ingreso') solo trae categorías de ingreso", True, all(c["tipo"] == "ingreso" for c in solo_ingreso))
    caso("list_categories(tipo='ingreso') no está vacío (seed.sql tiene categorías de ingreso)", True, len(solo_ingreso) > 0)

    solo_egreso = svc.list_categories(tipo="egreso")
    caso("list_categories(tipo='egreso') solo trae categorías de egreso", True, all(c["tipo"] == "egreso" for c in solo_egreso))

    print("\n--- list_categories() — incluir_inactivas ---")
    categoria_a_desactivar = todas[0]
    manager.execute("UPDATE categorias SET activa = 0 WHERE id = ?;", (categoria_a_desactivar["id"],))

    activas_tras_desactivar = svc.list_categories()
    caso(
        "list_categories() default ya no incluye la categoría recién desactivada",
        False,
        categoria_a_desactivar["id"] in [c["id"] for c in activas_tras_desactivar],
    )
    todas_incluyendo_inactivas = svc.list_categories(incluir_inactivas=True)
    caso(
        "list_categories(incluir_inactivas=True) sigue trayendo la categoría desactivada",
        True,
        categoria_a_desactivar["id"] in [c["id"] for c in todas_incluyendo_inactivas],
    )

    print("\n--- get_category() ---")
    primera = todas[1]  # una que sigue activa
    encontrada = svc.get_category(primera["id"])
    caso("get_category() encuentra una categoría existente", primera["subcategoria"], encontrada["subcategoria"] if encontrada else None)
    caso("get_category() de un id inexistente devuelve None", None, svc.get_category(999999))

    manager.desconectar()

    print(f"\n--- Resumen: {casos_ok}/{casos_total} casos OK ---")


if __name__ == "__main__":
    main()
