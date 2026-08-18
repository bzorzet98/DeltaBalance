"""
verify/presupuestos_ingresos_empleos/verify_ingresos_proyectados_service.py

Verifica IngresosProyectadosService (services/ingresos_proyectados_service.py)
— creado desde cero en Fase 2, bloque INGRESOS PROYECTADOS paso 2b, sin
comportamiento previo que replicar. Cubre create() (camino feliz + las 4
validaciones: concepto vacío, monto<=0, mes fuera de rango, moneda
inexistente), get()/list_for_period(), update() con el sentinel NO_CAMBIAR
(igual profundidad que el repositorio: un campo cambia, el resto mantiene
su valor previo), mark_partial() (camino feliz + el caso de error
monto>=estimado), mark_collected() (con y sin monto explícito), y el
bloqueo de re-transición sobre un ingreso ya 'cobrado'
(IngresoYaCobradoError) — la decisión del punto 7 de la consigna.

Correlo con:
    python verify/presupuestos_ingresos_empleos/verify_ingresos_proyectados_service.py
"""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from verify._dummy_db import crear_dummy_db
from db.database import DatabaseManager
from services.ingresos_proyectados_service import (
    IngresosProyectadosService,
    IngresoProyectadoNotFoundError,
    CurrencyNotFoundError,
    IngresoYaCobradoError,
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
    svc = IngresosProyectadosService(manager)

    moneda_ars = manager.fetchone("SELECT id FROM monedas WHERE codigo = 'ARS';")["id"]
    moneda_usd = manager.fetchone("SELECT id FROM monedas WHERE codigo = 'USD';")["id"]

    print("--- create() — camino feliz ---")
    resultado = svc.create(concepto="Freelance", mes=1, anio=2026, monto_estimado_minor=200000, moneda_id=moneda_ars)
    caso("create() devuelve success=True", True, resultado.success)
    caso("create() devuelve un entity_id numérico", True, isinstance(resultado.entity_id, int))
    ingreso_1 = resultado.entity_id

    fila = svc.get(ingreso_1)
    caso("create() persiste concepto", "Freelance", fila["concepto"])
    caso("create() deja estado='pendiente' (default de columna)", "pendiente", fila["estado"])
    caso("create() arranca monto_percibido_minor en 0", 0, fila["monto_percibido_minor"])

    print("\n--- create() — validaciones de negocio ---")
    caso_excepcion(
        "create() con concepto vacío lanza ValueError",
        ValueError,
        lambda: svc.create(concepto="   ", mes=1, anio=2026, monto_estimado_minor=1000, moneda_id=moneda_ars),
    )
    caso_excepcion(
        "create() con monto_estimado_minor<=0 lanza ValueError",
        ValueError,
        lambda: svc.create(concepto="X", mes=1, anio=2026, monto_estimado_minor=0, moneda_id=moneda_ars),
    )
    caso_excepcion(
        "create() con mes fuera de rango lanza ValueError",
        ValueError,
        lambda: svc.create(concepto="X", mes=13, anio=2026, monto_estimado_minor=1000, moneda_id=moneda_ars),
    )
    caso_excepcion(
        "create() con moneda_id inexistente lanza CurrencyNotFoundError",
        CurrencyNotFoundError,
        lambda: svc.create(concepto="X", mes=1, anio=2026, monto_estimado_minor=1000, moneda_id=999999),
    )

    print("\n--- get() / list_for_period() ---")
    caso("get() de un id inexistente devuelve None", None, svc.get(999999))

    ingreso_2 = svc.create(concepto="Reintegro", mes=1, anio=2026, monto_estimado_minor=50000, moneda_id=moneda_usd).entity_id
    ingreso_3 = svc.create(concepto="Venta usado", mes=2, anio=2026, monto_estimado_minor=30000, moneda_id=moneda_ars).entity_id

    listado_enero = svc.list_for_period(1, 2026)
    ids_enero = [r["id"] for r in listado_enero]
    caso("list_for_period(1, 2026) incluye ingreso_1 e ingreso_2", True, ingreso_1 in ids_enero and ingreso_2 in ids_enero)
    caso("list_for_period(1, 2026) excluye ingreso_3 (es de febrero)", False, ingreso_3 in ids_enero)

    print("\n--- update() — sentinel NO_CAMBIAR ---")
    caso_excepcion(
        "update() sobre id inexistente lanza IngresoProyectadoNotFoundError",
        IngresoProyectadoNotFoundError,
        lambda: svc.update(999999, concepto="X"),
    )

    res_update_1 = svc.update(ingreso_1, monto_estimado_minor=250000)
    caso("update(monto_estimado_minor=...) devuelve success=True", True, res_update_1.success)
    fila_1 = svc.get(ingreso_1)
    caso("update(monto_estimado_minor=...) solo: el monto cambia", 250000, fila_1["monto_estimado_minor"])
    caso("update(monto_estimado_minor=...) solo: concepto mantiene su valor previo (NO_CAMBIAR)", "Freelance", fila_1["concepto"])
    caso("update(monto_estimado_minor=...) solo: moneda_id mantiene su valor previo (NO_CAMBIAR)", moneda_ars, fila_1["moneda_id"])

    svc.update(ingreso_1, concepto="Freelance (renovado)", moneda_id=moneda_usd)
    fila_1_v2 = svc.get(ingreso_1)
    caso("update(concepto=..., moneda_id=...): ambos cambian", True, fila_1_v2["concepto"] == "Freelance (renovado)" and fila_1_v2["moneda_id"] == moneda_usd)
    caso("update(concepto=..., moneda_id=...): monto_estimado_minor mantiene su valor previo", 250000, fila_1_v2["monto_estimado_minor"])

    sin_cambios = svc.update(ingreso_1)
    caso("update() sin ningún campo devuelve success=False", False, sin_cambios.success)

    print("\n--- update() — validaciones de negocio ---")
    caso_excepcion(
        "update() con concepto vacío lanza ValueError",
        ValueError,
        lambda: svc.update(ingreso_1, concepto="   "),
    )
    caso_excepcion(
        "update() con monto_estimado_minor<=0 lanza ValueError",
        ValueError,
        lambda: svc.update(ingreso_1, monto_estimado_minor=0),
    )
    caso_excepcion(
        "update() con moneda_id inexistente lanza CurrencyNotFoundError",
        CurrencyNotFoundError,
        lambda: svc.update(ingreso_1, moneda_id=999999),
    )

    print("\n--- mark_partial() — camino feliz ---")
    res_partial = svc.mark_partial(ingreso_2, monto_percibido_minor=20000)
    caso("mark_partial() devuelve success=True", True, res_partial.success)
    fila_2 = svc.get(ingreso_2)
    caso("mark_partial() deja estado='parcial'", "parcial", fila_2["estado"])
    caso("mark_partial() persiste monto_percibido_minor", 20000, fila_2["monto_percibido_minor"])

    print("\n--- mark_partial() — se puede recibir en más de una cuota parcial (no bloqueado) ---")
    res_partial_2 = svc.mark_partial(ingreso_2, monto_percibido_minor=35000)
    caso("mark_partial() de nuevo sobre un 'parcial' existente devuelve success=True", True, res_partial_2.success)
    caso("mark_partial() de nuevo actualiza monto_percibido_minor", 35000, svc.get(ingreso_2)["monto_percibido_minor"])

    print("\n--- mark_partial() — validaciones de negocio ---")
    caso_excepcion(
        "mark_partial() sobre id inexistente lanza IngresoProyectadoNotFoundError",
        IngresoProyectadoNotFoundError,
        lambda: svc.mark_partial(999999, monto_percibido_minor=100),
    )
    caso_excepcion(
        "mark_partial() con monto_percibido_minor<=0 lanza ValueError",
        ValueError,
        lambda: svc.mark_partial(ingreso_2, monto_percibido_minor=0),
    )
    caso_excepcion(
        "mark_partial() con monto_percibido_minor >= monto_estimado_minor lanza ValueError (debería ser mark_collected)",
        ValueError,
        lambda: svc.mark_partial(ingreso_2, monto_percibido_minor=50000),
    )
    caso_excepcion(
        "mark_partial() con monto_percibido_minor > monto_estimado_minor también lanza ValueError",
        ValueError,
        lambda: svc.mark_partial(ingreso_2, monto_percibido_minor=999999),
    )

    print("\n--- mark_collected() — sin monto explícito: usa el estimado ---")
    res_collected_1 = svc.mark_collected(ingreso_3)
    caso("mark_collected() sin monto devuelve success=True", True, res_collected_1.success)
    fila_3 = svc.get(ingreso_3)
    caso("mark_collected() sin monto deja estado='cobrado'", "cobrado", fila_3["estado"])
    caso("mark_collected() sin monto usa monto_estimado_minor como percibido", 30000, fila_3["monto_percibido_minor"])

    print("\n--- mark_collected() — con monto explícito, puede diferir del estimado ---")
    ingreso_4 = svc.create(concepto="Bono", mes=3, anio=2026, monto_estimado_minor=80000, moneda_id=moneda_ars).entity_id
    res_collected_2 = svc.mark_collected(ingreso_4, monto_percibido_minor=95000)
    caso("mark_collected() con monto explícito devuelve success=True", True, res_collected_2.success)
    fila_4 = svc.get(ingreso_4)
    caso("mark_collected() con monto explícito deja estado='cobrado'", "cobrado", fila_4["estado"])
    caso("mark_collected() con monto explícito persiste el monto pasado (distinto al estimado, válido)", 95000, fila_4["monto_percibido_minor"])

    print("\n--- mark_collected() — validaciones de negocio ---")
    caso_excepcion(
        "mark_collected() sobre id inexistente lanza IngresoProyectadoNotFoundError",
        IngresoProyectadoNotFoundError,
        lambda: svc.mark_collected(999999),
    )
    ingreso_5 = svc.create(concepto="Otro", mes=4, anio=2026, monto_estimado_minor=1000, moneda_id=moneda_ars).entity_id
    caso_excepcion(
        "mark_collected() con monto_percibido_minor<=0 lanza ValueError",
        ValueError,
        lambda: svc.mark_collected(ingreso_5, monto_percibido_minor=0),
    )

    print("\n--- Bloqueo de re-transición sobre un ingreso ya 'cobrado' (punto 7) ---")
    caso_excepcion(
        "mark_partial() sobre un ingreso ya 'cobrado' lanza IngresoYaCobradoError",
        IngresoYaCobradoError,
        lambda: svc.mark_partial(ingreso_3, monto_percibido_minor=10000),
    )
    caso_excepcion(
        "mark_collected() sobre un ingreso ya 'cobrado' lanza IngresoYaCobradoError",
        IngresoYaCobradoError,
        lambda: svc.mark_collected(ingreso_3, monto_percibido_minor=1),
    )
    caso_excepcion(
        "mark_collected() sobre un ingreso ya 'cobrado' (sin monto explícito) también lanza IngresoYaCobradoError",
        IngresoYaCobradoError,
        lambda: svc.mark_collected(ingreso_4),
    )

    print("\n--- update() SIGUE disponible sobre un ingreso ya 'cobrado' (vía de corrección) ---")
    res_update_cobrado = svc.update(ingreso_3, concepto="Venta usado (corregido)")
    caso("update() sobre un ingreso 'cobrado' funciona (no está bloqueado, a diferencia del estado)", True, res_update_cobrado.success)
    caso("update() sobre un ingreso 'cobrado' persiste el cambio", "Venta usado (corregido)", svc.get(ingreso_3)["concepto"])
    caso("update() sobre un ingreso 'cobrado' no toca su estado", "cobrado", svc.get(ingreso_3)["estado"])

    manager.desconectar()

    print(f"\n--- Resumen: {casos_ok}/{casos_total} casos OK ---")


if __name__ == "__main__":
    main()
