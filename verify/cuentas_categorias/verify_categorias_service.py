"""
verify/cuentas_categorias/verify_categorias_service.py

Verifica CategoriasService (services/categorias_service.py). Cubre lo que ya
cubría antes (list_categories() sin filtro/filtrado por tipo/incluir_inactivas,
get_category() existe/no existe) más lo agregado para la gestión completa de
categorías: create_category()/update_category()/deactivate_category()/
activate_category() en su camino feliz, y el bloqueo de update/deactivate
sobre cada categoría protegida (CATEGORIAS_PROTEGIDAS en categorias_service.py)
identificada en la auditoría: INGRESOS · Sueldo y
MOVIMIENTO CAPITAL · Autotransferencia.

Correlo con:
    python verify/cuentas_categorias/verify_categorias_service.py
"""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from verify._dummy_db import crear_dummy_db
from db.database import DatabaseManager
from services.categorias_service import (
    CategoriasService,
    CategoriasError,
    CategoryNotFoundError,
    CategoryProtegidaError,
)


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
    categoria_a_desactivar = next(c for c in todas if (c["categoria_principal"], c["subcategoria"]) not in {
        ("INGRESOS", "Sueldo"), ("MOVIMIENTO CAPITAL", "Autotransferencia"),
    })
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
    manager.execute("UPDATE categorias SET activa = 1 WHERE id = ?;", (categoria_a_desactivar["id"],))

    print("\n--- get_category() ---")
    primera = next(c for c in todas if c["id"] != categoria_a_desactivar["id"])
    encontrada = svc.get_category(primera["id"])
    caso("get_category() encuentra una categoría existente", primera["subcategoria"], encontrada["subcategoria"] if encontrada else None)
    caso("get_category() de un id inexistente devuelve None", None, svc.get_category(999999))

    print("\n--- create_category() — camino feliz ---")
    resultado_creada = svc.create_category("EGRESOS VARIABLES", "Verify Nueva Categoria", "egreso")
    caso("create_category() devuelve success=True", True, resultado_creada.success)
    caso("create_category() devuelve un categoria_id numérico", True, isinstance(resultado_creada.categoria_id, int))
    creada = svc.get_category(resultado_creada.categoria_id)
    caso("la categoría creada tiene el nombre esperado", "Verify Nueva Categoria", creada["subcategoria"] if creada else None)
    caso("la categoría creada nace activa", 1, creada["activa"] if creada else None)

    print("\n--- create_category() — validaciones ---")
    try:
        svc.create_category("", "X", "egreso")
        caso("create_category() rechaza categoria_principal vacío", "CategoriasError", "no lanzó excepción")
    except CategoriasError:
        caso("create_category() rechaza categoria_principal vacío", "CategoriasError", "CategoriasError")

    try:
        svc.create_category("EGRESOS VARIABLES", "Otra", "tipo_invalido")
        caso("create_category() rechaza tipo inválido", "CategoriasError", "no lanzó excepción")
    except CategoriasError:
        caso("create_category() rechaza tipo inválido", "CategoriasError", "CategoriasError")

    try:
        svc.create_category("EGRESOS VARIABLES", "Verify Nueva Categoria", "egreso")
        caso("create_category() rechaza duplicado (UNIQUE)", "CategoriasError", "no lanzó excepción")
    except CategoriasError:
        caso("create_category() rechaza duplicado (UNIQUE)", "CategoriasError", "CategoriasError")

    print("\n--- update_category() — camino feliz ---")
    resultado_update = svc.update_category(resultado_creada.categoria_id, subcategoria="Verify Categoria Renombrada")
    caso("update_category() devuelve success=True", True, resultado_update.success)
    actualizada = svc.get_category(resultado_creada.categoria_id)
    caso("update_category() cambió el nombre", "Verify Categoria Renombrada", actualizada["subcategoria"] if actualizada else None)
    caso("update_category() no tocó categoria_principal (NO_CAMBIAR)", "EGRESOS VARIABLES", actualizada["categoria_principal"] if actualizada else None)

    print("\n--- update_category() — categoría inexistente ---")
    try:
        svc.update_category(999999, subcategoria="X")
        caso("update_category() sobre id inexistente lanza CategoryNotFoundError", "CategoryNotFoundError", "no lanzó excepción")
    except CategoryNotFoundError:
        caso("update_category() sobre id inexistente lanza CategoryNotFoundError", "CategoryNotFoundError", "CategoryNotFoundError")

    print("\n--- deactivate_category() / activate_category() — camino feliz ---")
    resultado_deact = svc.deactivate_category(resultado_creada.categoria_id)
    caso("deactivate_category() devuelve success=True", True, resultado_deact.success)
    desactivada = svc.get_category(resultado_creada.categoria_id)
    caso("deactivate_category() dejó activa = 0", 0, desactivada["activa"] if desactivada else None)
    caso("deactivate_category() no exige uso/saldo cero (la fila sigue existiendo)", True, desactivada is not None)

    resultado_act = svc.activate_category(resultado_creada.categoria_id)
    caso("activate_category() devuelve success=True", True, resultado_act.success)
    reactivada = svc.get_category(resultado_creada.categoria_id)
    caso("activate_category() dejó activa = 1 otra vez", 1, reactivada["activa"] if reactivada else None)

    print("\n--- Categorías protegidas: INGRESOS · Sueldo ---")
    sueldo = manager.fetchone(
        "SELECT * FROM categorias WHERE categoria_principal = 'INGRESOS' AND subcategoria = 'Sueldo';"
    )
    caso("la categoría 'Sueldo' existe en el seed (precondición del caso)", True, sueldo is not None)
    if sueldo is not None:
        try:
            svc.update_category(sueldo["id"], subcategoria="Sueldo Neto")
            caso("update_category() sobre 'Sueldo' lanza CategoryProtegidaError", "CategoryProtegidaError", "no lanzó excepción")
        except CategoryProtegidaError:
            caso("update_category() sobre 'Sueldo' lanza CategoryProtegidaError", "CategoryProtegidaError", "CategoryProtegidaError")

        try:
            svc.deactivate_category(sueldo["id"])
            caso("deactivate_category() sobre 'Sueldo' lanza CategoryProtegidaError", "CategoryProtegidaError", "no lanzó excepción")
        except CategoryProtegidaError:
            caso("deactivate_category() sobre 'Sueldo' lanza CategoryProtegidaError", "CategoryProtegidaError", "CategoryProtegidaError")

    print("\n--- Categorías protegidas: MOVIMIENTO CAPITAL · Autotransferencia ---")
    autotransferencia = manager.fetchone(
        "SELECT * FROM categorias WHERE categoria_principal = 'MOVIMIENTO CAPITAL' AND subcategoria = 'Autotransferencia';"
    )
    caso("la categoría 'Autotransferencia' existe en el seed (precondición del caso)", True, autotransferencia is not None)
    if autotransferencia is not None:
        try:
            svc.update_category(autotransferencia["id"], categoria_principal="OTRO")
            caso("update_category() sobre 'Autotransferencia' lanza CategoryProtegidaError", "CategoryProtegidaError", "no lanzó excepción")
        except CategoryProtegidaError:
            caso("update_category() sobre 'Autotransferencia' lanza CategoryProtegidaError", "CategoryProtegidaError", "CategoryProtegidaError")

        try:
            svc.deactivate_category(autotransferencia["id"])
            caso("deactivate_category() sobre 'Autotransferencia' lanza CategoryProtegidaError", "CategoryProtegidaError", "no lanzó excepción")
        except CategoryProtegidaError:
            caso("deactivate_category() sobre 'Autotransferencia' lanza CategoryProtegidaError", "CategoryProtegidaError", "CategoryProtegidaError")

        caso(
            "activate_category() sobre una protegida SÍ está permitido (ya está activa, no debe fallar)",
            True,
            svc.activate_category(autotransferencia["id"]).success,
        )

    manager.desconectar()

    print(f"\n--- Resumen: {casos_ok}/{casos_total} casos OK ---")


if __name__ == "__main__":
    main()
