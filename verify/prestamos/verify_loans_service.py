"""
verify/prestamos/verify_loans_service.py

Verifica LoansService (services/loans_service.py) — creado desde cero en
Fase 2, bloque PRESTAMOS paso 2, sin comportamiento previo que replicar.

Cubre create_loan() en los dos sistemas de amortización:
  - Francés: TODAS las cuotas con el mismo monto_total_minor (cuota fija,
    exacta en esta implementación — se redondea una sola vez y se
    reutiliza en las n filas), interés decreciente, capital creciente,
    capital+interés=total en cada fila, y el caso borde tasa_anual_bp=0
    (capital repartido en partes iguales, sin interés).
  - Alemán: capital elegido para dividir EXACTO (1200000/12=100000), lo
    que permite verificar valores exactos fila por fila, no solo
    invariantes: capital fijo=100000, interés decreciente de 12000 a 1000
    de a 1000, total decreciente de 112000 a 101000.

También cubre las validaciones de create_loan(), el rollover de año en el
cálculo de mes/anio por cuota, get_schedule() en orden, adjust_installment()
(incluido el cálculo automático de total cuando se pasan capital+interés),
mark_installment_paid(), update_loan() con NO_CAMBIAR, y cancel_loan()
confirmando que las cuotas siguen existiendo.

Correlo con:
    python verify/prestamos/verify_loans_service.py
"""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from verify._dummy_db import crear_dummy_db
from db.database import DatabaseManager
from services.loans_service import (
    LoansService,
    LoansError,
    PrestamoNotFoundError,
    CuotaPrestamoNotFoundError,
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
    svc = LoansService(manager)

    moneda_ars = manager.fetchone("SELECT id FROM monedas WHERE codigo = 'ARS';")["id"]
    cuenta_1 = manager.fetchone("SELECT id FROM cuentas LIMIT 1;")["id"]

    # ============================================================
    # create_loan() — sistema FRANCÉS (capital=1200000, tasa=12% anual, 12 cuotas)
    # ============================================================
    print("--- create_loan() — sistema francés ---")
    resultado_frances = svc.create_loan(
        entidad="Banco Galicia", tipo="hipotecario", capital_original_minor=1200000,
        tasa_anual_bp=1200, sistema_amortizacion="frances", moneda_id=moneda_ars,
        fecha_inicio="2026-01-01", plazo_meses=12,
    )
    caso("create_loan() francés: success=True", True, resultado_frances.success)
    caso("create_loan() francés: crea 12 cuotas", 12, resultado_frances.data["cuotas_creadas"])
    prestamo_frances = resultado_frances.entity_id

    schedule_frances = svc.get_schedule(prestamo_frances)
    caso("get_schedule() francés: 12 cuotas", 12, len(schedule_frances))
    caso(
        "get_schedule() las trae en orden ASC por numero_cuota (1..12)",
        list(range(1, 13)),
        [c["numero_cuota"] for c in schedule_frances],
    )

    totales_frances = [c["monto_total_minor"] for c in schedule_frances]
    caso("sistema francés: TODAS las cuotas tienen el mismo monto_total_minor (cuota fija, exacta)", True, len(set(totales_frances)) == 1)

    intereses_frances = [c["monto_interes_minor"] for c in schedule_frances]
    caso(
        "sistema francés: el interés decrece ESTRICTAMENTE cuota a cuota",
        True,
        all(intereses_frances[k] > intereses_frances[k + 1] for k in range(len(intereses_frances) - 1)),
    )

    capitales_frances = [c["monto_capital_minor"] for c in schedule_frances]
    caso(
        "sistema francés: el capital crece ESTRICTAMENTE cuota a cuota (inverso del interés)",
        True,
        all(capitales_frances[k] < capitales_frances[k + 1] for k in range(len(capitales_frances) - 1)),
    )

    caso(
        "sistema francés: monto_capital_minor + monto_interes_minor = monto_total_minor en CADA cuota",
        True,
        all(c["monto_capital_minor"] + c["monto_interes_minor"] == c["monto_total_minor"] for c in schedule_frances),
    )

    suma_capital_frances = sum(capitales_frances)
    caso(
        "sistema francés: la suma del capital de las 12 cuotas queda cerca de capital_original_minor "
        "(tolerancia generosa de redondeo — hasta ~0.5 unidad de error por cada redondeo de cuota_total "
        "y de interés, 12 cuotas, sin reconciliación forzada)",
        True,
        abs(suma_capital_frances - 1200000) <= 20,
    )

    # ============================================================
    # create_loan() — sistema ALEMÁN (mismos parámetros, capital divide exacto)
    # ============================================================
    print("\n--- create_loan() — sistema alemán ---")
    resultado_aleman = svc.create_loan(
        entidad="Banco Nación", tipo="prendario", capital_original_minor=1200000,
        tasa_anual_bp=1200, sistema_amortizacion="aleman", moneda_id=moneda_ars,
        fecha_inicio="2026-01-01", plazo_meses=12,
    )
    prestamo_aleman = resultado_aleman.entity_id
    schedule_aleman = svc.get_schedule(prestamo_aleman)
    caso("get_schedule() alemán: 12 cuotas", 12, len(schedule_aleman))

    capitales_aleman = [c["monto_capital_minor"] for c in schedule_aleman]
    caso(
        "sistema alemán: TODAS las cuotas tienen el mismo monto_capital_minor = 100000 (1200000/12 exacto)",
        [100000] * 12,
        capitales_aleman,
    )

    intereses_aleman = [c["monto_interes_minor"] for c in schedule_aleman]
    caso(
        "sistema alemán: el interés decrece de 12000 a 1000, de a 1000 por cuota (saldo baja 100000 cada vez, tasa 1%)",
        list(range(12000, 0, -1000)),
        intereses_aleman,
    )

    totales_aleman = [c["monto_total_minor"] for c in schedule_aleman]
    caso(
        "sistema alemán: monto_total_minor decrece de 112000 a 101000 (capital fijo + interés decreciente)",
        list(range(112000, 100000, -1000)),
        totales_aleman,
    )

    # ============================================================
    # create_loan() — caso borde: tasa_anual_bp=0 (francés)
    # ============================================================
    print("\n--- create_loan() — caso borde tasa_anual_bp=0 (francés) ---")
    resultado_tasa_cero = svc.create_loan(
        entidad="Banco Sin Interés", tipo="personal", capital_original_minor=1200000,
        tasa_anual_bp=0, sistema_amortizacion="frances", moneda_id=moneda_ars,
        fecha_inicio="2026-01-01", plazo_meses=12,
    )
    schedule_tasa_cero = svc.get_schedule(resultado_tasa_cero.entity_id)
    caso(
        "tasa_anual_bp=0 (francés): capital fijo = 100000 en las 12 cuotas (sin la fórmula estándar, que dividiría por cero)",
        [100000] * 12,
        [c["monto_capital_minor"] for c in schedule_tasa_cero],
    )
    caso("tasa_anual_bp=0 (francés): interés = 0 en las 12 cuotas", [0] * 12, [c["monto_interes_minor"] for c in schedule_tasa_cero])
    caso("tasa_anual_bp=0 (francés): total = 100000 en las 12 cuotas (igual al capital, sin interés)", [100000] * 12, [c["monto_total_minor"] for c in schedule_tasa_cero])

    # ============================================================
    # create_loan() — validaciones
    # ============================================================
    print("\n--- create_loan() — validaciones ---")
    caso_excepcion(
        "create_loan() con moneda_id inexistente lanza LoansError",
        LoansError,
        lambda: svc.create_loan(
            entidad="X", tipo="personal", capital_original_minor=1000, tasa_anual_bp=1000,
            sistema_amortizacion="frances", moneda_id=999999, fecha_inicio="2026-01-01", plazo_meses=6,
        ),
    )
    caso_excepcion(
        "create_loan() con capital_original_minor<=0 lanza ValueError",
        ValueError,
        lambda: svc.create_loan(
            entidad="X", tipo="personal", capital_original_minor=0, tasa_anual_bp=1000,
            sistema_amortizacion="frances", moneda_id=moneda_ars, fecha_inicio="2026-01-01", plazo_meses=6,
        ),
    )
    caso_excepcion(
        "create_loan() con plazo_meses<=0 lanza ValueError",
        ValueError,
        lambda: svc.create_loan(
            entidad="X", tipo="personal", capital_original_minor=1000, tasa_anual_bp=1000,
            sistema_amortizacion="frances", moneda_id=moneda_ars, fecha_inicio="2026-01-01", plazo_meses=0,
        ),
    )
    caso_excepcion(
        "create_loan() con tasa_anual_bp negativa lanza ValueError",
        ValueError,
        lambda: svc.create_loan(
            entidad="X", tipo="personal", capital_original_minor=1000, tasa_anual_bp=-100,
            sistema_amortizacion="frances", moneda_id=moneda_ars, fecha_inicio="2026-01-01", plazo_meses=6,
        ),
    )
    caso_excepcion(
        "create_loan() con cuenta_debito_id inexistente lanza LoansError",
        LoansError,
        lambda: svc.create_loan(
            entidad="X", tipo="personal", capital_original_minor=1000, tasa_anual_bp=1000,
            sistema_amortizacion="frances", moneda_id=moneda_ars, fecha_inicio="2026-01-01", plazo_meses=6,
            cuenta_debito_id=999999,
        ),
    )

    # ============================================================
    # create_loan() — rollover de año en mes/anio por cuota
    # ============================================================
    print("\n--- create_loan() — rollover de año ---")
    resultado_rollover = svc.create_loan(
        entidad="Rollover Bank", tipo="personal", capital_original_minor=300000,
        tasa_anual_bp=1200, sistema_amortizacion="frances", moneda_id=moneda_ars,
        fecha_inicio="2026-11-01", plazo_meses=3,
    )
    schedule_rollover = svc.get_schedule(resultado_rollover.entity_id)
    caso("rollover: cuota 1 cae en 11/2026 (mes de fecha_inicio)", (11, 2026), (schedule_rollover[0]["mes"], schedule_rollover[0]["anio"]))
    caso("rollover: cuota 2 cae en 12/2026", (12, 2026), (schedule_rollover[1]["mes"], schedule_rollover[1]["anio"]))
    caso("rollover: cuota 3 cae en 1/2027 (rollover de año)", (1, 2027), (schedule_rollover[2]["mes"], schedule_rollover[2]["anio"]))

    # ============================================================
    # list_loans() / get_loan()
    # ============================================================
    print("\n--- list_loans() / get_loan() ---")
    caso("get_loan() encuentra el préstamo francés", "Banco Galicia", svc.get_loan(prestamo_frances)["entidad"])
    caso("get_loan() de un id inexistente devuelve None", None, svc.get_loan(999999))

    todos_hipotecarios = svc.list_loans(tipo="hipotecario")
    caso("list_loans(tipo='hipotecario') incluye el préstamo francés", True, prestamo_frances in [p["id"] for p in todos_hipotecarios])

    # ============================================================
    # adjust_installment()
    # ============================================================
    print("\n--- adjust_installment() ---")
    cuota_ajustar = schedule_frances[2]["id"]  # cuota #3
    otras_antes = {
        c["id"]: (c["monto_capital_minor"], c["monto_interes_minor"], c["monto_total_minor"])
        for c in schedule_frances if c["id"] != cuota_ajustar
    }

    resultado_ajuste = svc.adjust_installment(cuota_ajustar, monto_interes_minor=9999)
    caso("adjust_installment() success=True", True, resultado_ajuste.success)

    schedule_tras_ajuste = svc.get_schedule(prestamo_frances)
    cuota_tras_ajuste = next(c for c in schedule_tras_ajuste if c["id"] == cuota_ajustar)
    caso("adjust_installment() reescribe monto_interes_minor", 9999, cuota_tras_ajuste["monto_interes_minor"])

    otras_despues = {
        c["id"]: (c["monto_capital_minor"], c["monto_interes_minor"], c["monto_total_minor"])
        for c in schedule_tras_ajuste if c["id"] != cuota_ajustar
    }
    caso("adjust_installment() no toca las demás cuotas del préstamo", otras_antes, otras_despues)

    otra_cuota_id = schedule_frances[5]["id"]  # cuota #6
    resultado_calculo = svc.adjust_installment(otra_cuota_id, monto_capital_minor=50000, monto_interes_minor=3000)
    caso("adjust_installment() con capital+interés sin total: lo calcula solo (50000+3000=53000)", 53000, resultado_calculo.data["monto_total_minor"])
    cuota_calculada = next(c for c in svc.get_schedule(prestamo_frances) if c["id"] == otra_cuota_id)
    caso("adjust_installment() persiste el total calculado", 53000, cuota_calculada["monto_total_minor"])

    caso_excepcion(
        "adjust_installment() con cuota_id inexistente lanza CuotaPrestamoNotFoundError",
        CuotaPrestamoNotFoundError,
        lambda: svc.adjust_installment(999999, monto_interes_minor=100),
    )
    caso_excepcion(
        "adjust_installment() sin ningún monto lanza ValueError",
        ValueError,
        lambda: svc.adjust_installment(cuota_ajustar),
    )

    # ============================================================
    # mark_installment_paid()
    # ============================================================
    print("\n--- mark_installment_paid() ---")
    cuota_pagar = schedule_frances[0]["id"]
    resultado_pago = svc.mark_installment_paid(cuota_pagar, fecha_pago="2026-02-01")
    caso("mark_installment_paid() success=True", True, resultado_pago.success)

    cuota_pagada = next(c for c in svc.get_schedule(prestamo_frances) if c["id"] == cuota_pagar)
    caso("mark_installment_paid() marca estado='pagado'", "pagado", cuota_pagada["estado"])
    caso("mark_installment_paid() persiste fecha_pago", "2026-02-01", cuota_pagada["fecha_pago"])

    caso_excepcion(
        "mark_installment_paid() con cuota_id inexistente lanza CuotaPrestamoNotFoundError",
        CuotaPrestamoNotFoundError,
        lambda: svc.mark_installment_paid(999999, fecha_pago="2026-01-01"),
    )

    # ============================================================
    # update_loan() — sentinel NO_CAMBIAR
    # ============================================================
    print("\n--- update_loan() ---")
    resultado_update = svc.update_loan(prestamo_frances, notas="Nota actualizada")
    caso("update_loan(notas=...) success=True", True, resultado_update.success)
    caso("update_loan(notas=...) persiste notas", "Nota actualizada", svc.get_loan(prestamo_frances)["notas"])

    svc.update_loan(prestamo_frances, cuenta_debito_id=cuenta_1)
    caso("update_loan(cuenta_debito_id=...) vincula la cuenta", cuenta_1, svc.get_loan(prestamo_frances)["cuenta_debito_id"])
    caso("update_loan(cuenta_debito_id=...) no tocó notas (NO_CAMBIAR)", "Nota actualizada", svc.get_loan(prestamo_frances)["notas"])

    caso_excepcion(
        "update_loan() con prestamo_id inexistente lanza PrestamoNotFoundError",
        PrestamoNotFoundError,
        lambda: svc.update_loan(999999, notas="x"),
    )
    caso_excepcion(
        "update_loan() con cuenta_debito_id inexistente lanza LoansError",
        LoansError,
        lambda: svc.update_loan(prestamo_frances, cuenta_debito_id=999999),
    )

    # ============================================================
    # cancel_loan()
    # ============================================================
    print("\n--- cancel_loan() ---")
    resultado_cancel = svc.cancel_loan(prestamo_frances)
    caso("cancel_loan() success=True", True, resultado_cancel.success)
    caso("cancel_loan() cambia el estado a 'cancelado'", "cancelado", svc.get_loan(prestamo_frances)["estado"])

    schedule_tras_cancelar = svc.get_schedule(prestamo_frances)
    caso("cancel_loan() NO borra las cuotas — las 12 siguen existiendo", 12, len(schedule_tras_cancelar))

    caso_excepcion(
        "cancel_loan() con prestamo_id inexistente lanza PrestamoNotFoundError",
        PrestamoNotFoundError,
        lambda: svc.cancel_loan(999999),
    )

    manager.desconectar()

    print(f"\n--- Resumen: {casos_ok}/{casos_total} casos OK ---")


if __name__ == "__main__":
    main()
