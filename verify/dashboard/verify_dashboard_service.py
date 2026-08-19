"""
verify/dashboard/verify_dashboard_service.py

Verifica DashboardService (services/dashboard_service.py) — creado desde
cero en Fase 5. Cubre get_patrimonio_total() (delega en
AccountsService.get_total_balance(), cuentas en dos monedas sin mezclar),
get_gasto_por_categoria() (varias transacciones en varias categorías,
confirmando el agrupamiento por categoría, el orden de mayor a menor, y un
caso de dos monedas en el mismo mes/categoría sin mezclarse), y
get_comparacion_presupuesto() con los tres casos: categoría con presupuesto
y gasto real, categoría con presupuesto sin gasto real (real_minor=0), y
categoría con gasto real pero sin presupuesto (excluida del resultado).

Correlo con:
    python verify/dashboard/verify_dashboard_service.py
"""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from verify._dummy_db import crear_dummy_db
from db.database import DatabaseManager
from services.accounts_service import AccountsService
from services.presupuestos_service import PresupuestosService
from services.dashboard_service import DashboardService


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

    accounts_svc = AccountsService(manager)
    presupuestos_svc = PresupuestosService(manager)
    dash_svc = DashboardService(manager)

    moneda_ars = manager.fetchone("SELECT id FROM monedas WHERE codigo = 'ARS';")["id"]
    moneda_usd = manager.fetchone("SELECT id FROM monedas WHERE codigo = 'USD';")["id"]

    cat_super = manager.fetchone(
        "SELECT id FROM categorias WHERE categoria_principal = 'EGRESOS VARIABLES' AND subcategoria = 'Supermercado';"
    )["id"]
    cat_salud = manager.fetchone(
        "SELECT id FROM categorias WHERE categoria_principal = 'EGRESOS VARIABLES' AND subcategoria = 'Salud';"
    )["id"]
    cat_alquiler = manager.fetchone(
        "SELECT id FROM categorias WHERE categoria_principal = 'EGRESOS FIJOS' AND subcategoria = 'Alquiler / Vivienda';"
    )["id"]
    cat_ingreso = manager.fetchone(
        "SELECT id FROM categorias WHERE tipo = 'ingreso' LIMIT 1;"
    )["id"]

    def insertar_transaccion(fecha, cuenta_id, categoria_id, moneda_id, tipo, monto_minor):
        manager.execute(
            """
            INSERT INTO transacciones (fecha, concepto, cuenta_id, categoria_id, moneda_id, tipo_movimiento, monto_minor)
            VALUES (?, ?, ?, ?, ?, ?, ?);
            """,
            (fecha, "Transacción de prueba", cuenta_id, categoria_id, moneda_id, tipo, monto_minor),
        )

    # ------------------------------------------------------------
    # get_patrimonio_total()
    # ------------------------------------------------------------
    print("--- get_patrimonio_total() — cuentas en dos monedas, sin mezclar ---")

    cuenta_ars = accounts_svc.create_account(nombre="Cuenta ARS", tipo="efectivo", monedas=[moneda_ars]).account_id
    cuenta_usd = accounts_svc.create_account(nombre="Cuenta USD", tipo="efectivo", monedas=[moneda_usd]).account_id
    insertar_transaccion("2026-05-01", cuenta_ars, cat_ingreso, moneda_ars, "ingreso", 100000)
    insertar_transaccion("2026-05-01", cuenta_usd, cat_ingreso, moneda_usd, "ingreso", 5000)

    patrimonio = dash_svc.get_patrimonio_total()
    caso("get_patrimonio_total() incluye ARS", 100000, patrimonio.get("ARS"))
    caso("get_patrimonio_total() incluye USD por separado", 5000, patrimonio.get("USD"))
    caso("get_patrimonio_total() es exactamente lo que devuelve AccountsService.get_total_balance()",
         accounts_svc.get_total_balance(), patrimonio)

    # ------------------------------------------------------------
    # get_gasto_por_categoria()
    # ------------------------------------------------------------
    print("\n--- get_gasto_por_categoria() — agrupamiento y orden ---")

    # Mayo 2026: dos transacciones en Supermercado (se suman), una en Salud,
    # una en Alquiler (fuera de mes: abril, no debe contar).
    insertar_transaccion("2026-05-05", cuenta_ars, cat_super, moneda_ars, "egreso", 30000)
    insertar_transaccion("2026-05-10", cuenta_ars, cat_super, moneda_ars, "egreso", 20000)
    insertar_transaccion("2026-05-12", cuenta_ars, cat_salud, moneda_ars, "egreso", 15000)
    insertar_transaccion("2026-05-15", cuenta_ars, cat_alquiler, moneda_ars, "egreso", 80000)
    insertar_transaccion("2026-04-28", cuenta_ars, cat_super, moneda_ars, "egreso", 999999)  # abril, no debe contar
    # Un ingreso no debe contar como gasto
    insertar_transaccion("2026-05-20", cuenta_ars, cat_ingreso, moneda_ars, "ingreso", 500000)

    gasto_mayo = dash_svc.get_gasto_por_categoria(5, 2026)
    por_categoria = {(g["categoria_id"], g["moneda_id"]): g["monto_total_minor"] for g in gasto_mayo}

    caso("get_gasto_por_categoria() suma las dos transacciones de Supermercado", 50000, por_categoria.get((cat_super, moneda_ars)))
    caso("get_gasto_por_categoria() trae Salud con su monto", 15000, por_categoria.get((cat_salud, moneda_ars)))
    caso("get_gasto_por_categoria() trae Alquiler con su monto", 80000, por_categoria.get((cat_alquiler, moneda_ars)))
    caso("get_gasto_por_categoria() no incluye la transacción de abril", True, sum(por_categoria.values()) == 50000 + 15000 + 80000)
    caso("get_gasto_por_categoria() no cuenta ingresos como gasto", True, cat_ingreso not in [g["categoria_id"] for g in gasto_mayo])

    montos_ordenados = [g["monto_total_minor"] for g in gasto_mayo]
    caso("get_gasto_por_categoria() ordena de mayor a menor", True, montos_ordenados == sorted(montos_ordenados, reverse=True))
    # El mayor gasto del mes es Alquiler (80000), por encima de Supermercado (50000) y Salud (15000).
    caso("get_gasto_por_categoria() el mayor gasto del mes es Alquiler, con su categoria_nombre correcto", "Alquiler / Vivienda", gasto_mayo[0]["categoria_nombre"])

    print("\n--- get_gasto_por_categoria() — dos monedas en la misma categoría/mes, sin mezclar ---")
    insertar_transaccion("2026-05-18", cuenta_usd, cat_super, moneda_usd, "egreso", 4000)
    gasto_mayo_v2 = dash_svc.get_gasto_por_categoria(5, 2026)
    por_categoria_v2 = {(g["categoria_id"], g["moneda_id"]): g["monto_total_minor"] for g in gasto_mayo_v2}
    caso("Supermercado en ARS mantiene su monto propio (no se mezcla con USD)", 50000, por_categoria_v2.get((cat_super, moneda_ars)))
    caso("Supermercado en USD aparece como entrada separada", 4000, por_categoria_v2.get((cat_super, moneda_usd)))
    caso("hay dos entradas distintas para Supermercado (una por moneda)",
         2, sum(1 for g in gasto_mayo_v2 if g["categoria_id"] == cat_super))

    caso_excepcion_mes_invalido = False
    try:
        dash_svc.get_gasto_por_categoria(13, 2026)
    except ValueError:
        caso_excepcion_mes_invalido = True
    caso("get_gasto_por_categoria() con mes fuera de rango lanza ValueError", True, caso_excepcion_mes_invalido)

    # ------------------------------------------------------------
    # get_comparacion_presupuesto()
    # ------------------------------------------------------------
    print("\n--- get_comparacion_presupuesto() — los tres casos ---")

    # Caso 1: presupuesto y gasto real (Supermercado, ARS: presupuesto 60000, gasto real 50000)
    presupuestos_svc.set_budget(categoria_id=cat_super, mes=5, anio=2026, moneda_id=moneda_ars, monto_estimado_minor=60000)

    # Caso 2: presupuesto sin gasto real (Alquiler ya tiene gasto — usemos una categoría sin ninguna
    # transacción en mayo: Salud SÍ tiene gasto, así que para "sin gasto" usamos otra categoría nueva).
    cat_educacion = manager.fetchone(
        "SELECT id FROM categorias WHERE categoria_principal = 'EGRESOS FIJOS' AND subcategoria = 'Educacion';"
    )["id"]
    presupuestos_svc.set_budget(categoria_id=cat_educacion, mes=5, anio=2026, moneda_id=moneda_ars, monto_estimado_minor=25000)

    # Caso 3: gasto real sin presupuesto — Alquiler tiene gasto (80000) pero nunca se le cargó presupuesto.

    comparacion = dash_svc.get_comparacion_presupuesto(5, 2026)
    por_cat_comparacion = {c["categoria_id"]: c for c in comparacion}

    caso("Caso 1 — Supermercado: estimado_minor correcto", 60000, por_cat_comparacion.get(cat_super, {}).get("estimado_minor"))
    caso("Caso 1 — Supermercado: real_minor toma el gasto en la MISMA moneda del presupuesto (ARS, no mezcla con el USD)", 50000, por_cat_comparacion.get(cat_super, {}).get("real_minor"))

    caso("Caso 2 — Educación: tiene presupuesto pero sin gasto real, entra con real_minor=0", True, cat_educacion in por_cat_comparacion)
    caso("Caso 2 — Educación: estimado_minor correcto", 25000, por_cat_comparacion.get(cat_educacion, {}).get("estimado_minor"))
    caso("Caso 2 — Educación: real_minor es 0", 0, por_cat_comparacion.get(cat_educacion, {}).get("real_minor"))

    caso("Caso 3 — Alquiler tiene gasto real (80000) pero SIN presupuesto: excluida del resultado", False, cat_alquiler in por_cat_comparacion)

    manager.desconectar()

    print(f"\n--- Resumen: {casos_ok}/{casos_total} casos OK ---")


if __name__ == "__main__":
    main()
