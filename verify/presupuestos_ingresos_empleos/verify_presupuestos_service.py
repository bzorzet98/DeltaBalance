"""
verify/presupuestos_ingresos_empleos/verify_presupuestos_service.py

Verifica PresupuestosService (services/presupuestos_service.py) — creado
desde cero en Fase 2, bloque PRESUPUESTOS paso 2a, sin comportamiento
previo que replicar. Cubre set_budget() (camino feliz + las 4
validaciones: categoría inexistente, moneda inexistente, monto<=0, mes
fuera de rango), get_budget()/list_budgets(), update_executed(), y el caso
más importante: copy_period() con solo_recurrentes=True (default, filtra
en el service) vs. solo_recurrentes=False (copia todo, delega al
repositorio).

También confirma que copy_period(solo_recurrentes=True) no sobreescribe un
presupuesto que ya existe en el destino — mismo criterio "no pisar" que
PresupuestosRepository.copiar_periodo() usa vía INSERT OR IGNORE (ver
docstring de copy_period() en el service).

Cubre además: list_budgeted_category_ids(), y formula_estimado (parámetro
nuevo de set_budget(), pasthrough directo a
PresupuestosRepository.upsert()): guardar con fórmula persiste el texto Y
el monto ya calculado; guardar sin fórmula deja la columna en NULL;
sobreescribir un presupuesto con fórmula con un set_budget() SIN
formula_estimado limpia la columna a NULL.

Correlo con:
    python verify/presupuestos_ingresos_empleos/verify_presupuestos_service.py
"""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from verify._dummy_db import crear_dummy_db
from db.database import DatabaseManager
from services.presupuestos_service import (
    PresupuestosService,
    CategoryNotFoundError,
    CurrencyNotFoundError,
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

    def caso_excepcion(descripcion: str, tipo_esperado, callable_) -> None:
        nonlocal casos_ok, casos_total
        casos_total += 1
        try:
            callable_()
            print(f"❌ {descripcion} — esperaba {tipo_esperado.__name__}, no se lanzó ninguna excepción")
        except tipo_esperado:
            casos_ok += 1
            print(f"✅ {descripcion} — lanzó {tipo_esperado.__name__} como se esperaba")
        except Exception as e:
            print(f"❌ {descripcion} — esperaba {tipo_esperado.__name__}, se lanzó {type(e).__name__}: {e!r}")

    db_path = crear_dummy_db()
    print(f"Dummy DB creada en: {db_path}\n")

    manager = DatabaseManager(db_path=db_path)
    manager.inicializar()
    svc = PresupuestosService(manager)

    categorias = manager.fetchall("SELECT id FROM categorias WHERE tipo = 'egreso' LIMIT 3;")
    cat_1, cat_2, cat_3 = categorias[0]["id"], categorias[1]["id"], categorias[2]["id"]
    moneda_ars = manager.fetchone("SELECT id FROM monedas WHERE codigo = 'ARS';")["id"]

    print("--- set_budget() — camino feliz ---")
    resultado = svc.set_budget(
        categoria_id=cat_1, mes=1, anio=2026, moneda_id=moneda_ars,
        monto_estimado_minor=100000, es_recurrente=True, notas="Alquiler",
    )
    caso("set_budget() devuelve success=True", True, resultado.success)
    caso("set_budget() devuelve filas_afectadas=1", 1, resultado.data["filas_afectadas"])

    fila = svc.get_budget(cat_1, 1, 2026)
    caso("set_budget() persiste el presupuesto", 100000, fila["monto_estimado_minor"])

    print("\n--- set_budget() — validaciones de negocio ---")
    caso_excepcion(
        "set_budget() con categoria_id inexistente lanza CategoryNotFoundError",
        CategoryNotFoundError,
        lambda: svc.set_budget(categoria_id=999999, mes=1, anio=2026, moneda_id=moneda_ars, monto_estimado_minor=1000),
    )
    caso_excepcion(
        "set_budget() con moneda_id inexistente lanza CurrencyNotFoundError",
        CurrencyNotFoundError,
        lambda: svc.set_budget(categoria_id=cat_1, mes=1, anio=2026, moneda_id=999999, monto_estimado_minor=1000),
    )
    caso_excepcion(
        "set_budget() con monto_estimado_minor<=0 lanza ValueError",
        ValueError,
        lambda: svc.set_budget(categoria_id=cat_1, mes=1, anio=2026, moneda_id=moneda_ars, monto_estimado_minor=0),
    )
    caso_excepcion(
        "set_budget() con mes fuera de rango lanza ValueError",
        ValueError,
        lambda: svc.set_budget(categoria_id=cat_1, mes=13, anio=2026, moneda_id=moneda_ars, monto_estimado_minor=1000),
    )

    print("\n--- get_budget() / list_budgets() ---")
    caso("get_budget() de un período sin presupuesto devuelve None", None, svc.get_budget(cat_1, 2, 2026))

    svc.set_budget(categoria_id=cat_2, mes=1, anio=2026, moneda_id=moneda_ars, monto_estimado_minor=50000)
    listado = svc.list_budgets(1, 2026)
    ids_listado = [r["categoria_id"] for r in listado]
    caso("list_budgets(1, 2026) incluye cat_1 y cat_2", True, cat_1 in ids_listado and cat_2 in ids_listado)
    caso("list_budgets() trae shape enriquecido (subcategoria vía JOIN)", True, "subcategoria" in listado[0].keys())

    print("\n--- update_executed() ---")
    res_ejecutado = svc.update_executed(cat_1, 1, 2026, monto_ejecutado_minor=60000)
    caso("update_executed() devuelve success=True", True, res_ejecutado.success)
    caso("update_executed() persiste el valor", 60000, svc.get_budget(cat_1, 1, 2026)["monto_ejecutado_minor"])
    caso("update_executed() no toca monto_estimado_minor", 100000, svc.get_budget(cat_1, 1, 2026)["monto_estimado_minor"])

    print("\n--- copy_period() — armando el período origen ---")
    svc.set_budget(categoria_id=cat_1, mes=3, anio=2026, moneda_id=moneda_ars, monto_estimado_minor=100000, es_recurrente=True, notas="Alquiler recurrente")
    svc.set_budget(categoria_id=cat_2, mes=3, anio=2026, moneda_id=moneda_ars, monto_estimado_minor=50000, es_recurrente=True, notas="Internet recurrente")
    svc.set_budget(categoria_id=cat_3, mes=3, anio=2026, moneda_id=moneda_ars, monto_estimado_minor=20000, es_recurrente=False, notas="Compra puntual, no recurrente")

    print("\n--- copy_period(solo_recurrentes=True) — default ---")
    res_copy_true = svc.copy_period(mes_origen=3, anio_origen=2026, mes_destino=4, anio_destino=2026)
    caso("copy_period(solo_recurrentes=True) devuelve success=True", True, res_copy_true.success)
    caso("copy_period(solo_recurrentes=True) copia exactamente 2 (los recurrentes)", 2, res_copy_true.data["copiados"])

    listado_destino_true = svc.list_budgets(4, 2026)
    ids_destino_true = [r["categoria_id"] for r in listado_destino_true]
    caso("copy_period(solo_recurrentes=True): el destino tiene exactamente 2 presupuestos", 2, len(listado_destino_true))
    caso("copy_period(solo_recurrentes=True): incluye cat_1 (recurrente)", True, cat_1 in ids_destino_true)
    caso("copy_period(solo_recurrentes=True): incluye cat_2 (recurrente)", True, cat_2 in ids_destino_true)
    caso("copy_period(solo_recurrentes=True): NO incluye cat_3 (no recurrente)", False, cat_3 in ids_destino_true)

    print("\n--- copy_period(solo_recurrentes=False) — sobre otro período destino ---")
    res_copy_false = svc.copy_period(mes_origen=3, anio_origen=2026, mes_destino=5, anio_destino=2026, solo_recurrentes=False)
    caso("copy_period(solo_recurrentes=False) devuelve success=True", True, res_copy_false.success)
    caso("copy_period(solo_recurrentes=False) copia los 3 (sin filtrar)", 3, res_copy_false.data["copiados"])

    listado_destino_false = svc.list_budgets(5, 2026)
    ids_destino_false = [r["categoria_id"] for r in listado_destino_false]
    caso("copy_period(solo_recurrentes=False): el destino tiene los 3", 3, len(listado_destino_false))
    caso("copy_period(solo_recurrentes=False): incluye cat_3 (a diferencia del caso filtrado)", True, cat_3 in ids_destino_false)

    print("\n--- copy_period(solo_recurrentes=True) — no sobreescribe lo que ya existe en el destino ---")
    svc.set_budget(categoria_id=cat_1, mes=6, anio=2026, moneda_id=moneda_ars, monto_estimado_minor=999999, notas="Ya existía en destino")
    res_copy_overlap = svc.copy_period(mes_origen=3, anio_origen=2026, mes_destino=6, anio_destino=2026)
    caso("copy_period() con overlap parcial solo copia la fila faltante (cat_2)", 1, res_copy_overlap.data["copiados"])
    caso(
        "copy_period() con overlap parcial NO sobreescribe cat_1 (ya existía en destino)",
        999999,
        svc.get_budget(cat_1, 6, 2026)["monto_estimado_minor"],
    )
    caso("copy_period() con overlap parcial SÍ copia cat_2 (faltaba)", 50000, svc.get_budget(cat_2, 6, 2026)["monto_estimado_minor"])

    print("\n--- list_budgeted_category_ids() ---")
    cat_sin_presupuesto = manager.fetchall("SELECT id FROM categorias WHERE tipo = 'egreso';")[-1]["id"]
    ids_presupuestadas = svc.list_budgeted_category_ids()
    caso("list_budgeted_category_ids() incluye cat_1 (presupuestada en varios períodos)", True, cat_1 in ids_presupuestadas)
    caso("list_budgeted_category_ids() incluye cat_2 (presupuestada)", True, cat_2 in ids_presupuestadas)
    caso(
        "list_budgeted_category_ids() no incluye una categoría de egreso que nunca se presupuestó",
        False,
        cat_sin_presupuesto in ids_presupuestadas,
    )

    print("\n--- set_budget() — formula_estimado: guardar con fórmula persiste AMBOS campos ---")
    cat_formula = manager.fetchall("SELECT id FROM categorias WHERE tipo = 'egreso';")[-2]["id"]
    res_con_formula = svc.set_budget(
        categoria_id=cat_formula, mes=1, anio=2026, moneda_id=moneda_ars,
        monto_estimado_minor=17700, formula_estimado="=15000+3200-500",
    )
    caso("set_budget() con formula_estimado devuelve success=True", True, res_con_formula.success)
    caso("set_budget() con formula_estimado devuelve formula_estimado en data", "=15000+3200-500", res_con_formula.data["formula_estimado"])
    fila_con_formula = svc.get_budget(cat_formula, 1, 2026)
    caso("set_budget() con formula_estimado persiste el monto_estimado_minor ya calculado", 17700, fila_con_formula["monto_estimado_minor"])
    caso("set_budget() con formula_estimado persiste el texto de la fórmula tal cual", "=15000+3200-500", fila_con_formula["formula_estimado"])

    print("\n--- set_budget() — formula_estimado: guardar SIN fórmula deja la columna en NULL ---")
    cat_sin_formula = manager.fetchall("SELECT id FROM categorias WHERE tipo = 'egreso';")[-3]["id"]
    svc.set_budget(categoria_id=cat_sin_formula, mes=1, anio=2026, moneda_id=moneda_ars, monto_estimado_minor=50000)
    fila_sin_formula = svc.get_budget(cat_sin_formula, 1, 2026)
    caso("set_budget() sin pasar formula_estimado (default None) deja la columna en NULL", None, fila_sin_formula["formula_estimado"])

    print("\n--- set_budget() — formula_estimado: sobreescribir con un número directo limpia la fórmula vieja a NULL ---")
    svc.set_budget(categoria_id=cat_formula, mes=1, anio=2026, moneda_id=moneda_ars, monto_estimado_minor=99999)  # sin formula_estimado
    fila_tras_sobreescribir = svc.get_budget(cat_formula, 1, 2026)
    caso("set_budget() sobre una fila que tenía fórmula: el nuevo monto_estimado_minor se persiste", 99999, fila_tras_sobreescribir["monto_estimado_minor"])
    caso(
        "set_budget() sobre una fila que tenía fórmula: al no pasar formula_estimado de nuevo, queda en NULL "
        "(no se arrastra una fórmula vieja asociada a un monto que ya no le corresponde)",
        None,
        fila_tras_sobreescribir["formula_estimado"],
    )

    manager.desconectar()

    print(f"\n--- Resumen: {casos_ok}/{casos_total} casos OK ---")


if __name__ == "__main__":
    main()
