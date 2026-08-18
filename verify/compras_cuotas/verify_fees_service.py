"""
verify/compras_cuotas/verify_fees_service.py

Verifica FeesService COMPLETO (no los repositorios pelados) después de la
migración a ComprasCuotasRepository/CuotasCreditoRepository/
ResumenesTarjetaRepository/ResumenCargosExtraRepository (Fase 2, bloque
COMPRAS_CUOTAS paso 2 + cierre del pendiente de cargos extra): que
create_purchase()/get_purchase()/list_purchases()/get_fees_for_purchase()/
open_statement()/confirm_fee()/close_statement()/pay_statement()/
get_statement()/list_statements()/cancel_purchase()/add_extra_charge()/
list_extra_charges()/remove_extra_charge() siguen funcionando end-to-end con
las validaciones y excepciones de negocio intactas (PurchaseNotFoundError,
StatementNotFoundError, FeeNotFoundError, StatementAlreadyPaidError,
StatementAlreadyClosedError, FeeAlreadyConfirmedError, FeesError, ValueError).

Además prueba explícitamente los cambios de comportamiento sancionados de
esta migración:
- close_statement() ahora CONSOLIDA de verdad monto_consumos_minor (sumando
  cuotas_credito reales), monto_impuestos_minor (sumando resumen_cargos_extra
  reales, agregados vía add_extra_charge() — ya expuesto por el service, no
  hace falta ir al repositorio directo) y porcentaje_impuesto_bp derivado —
  mismo escenario numérico que verify_resumenes_tarjeta_repository.py.
- pay_statement() ahora requiere monto_pagado_minor y lo persiste en
  monto_total_pagado_minor.
- add_extra_charge()/remove_extra_charge() sobre un resumen 'abierto' NO
  tocan monto_impuestos_minor/porcentaje_impuesto_bp de resumenes_tarjeta —
  eso es responsabilidad exclusiva de close_statement(). Se bloquean sobre
  un resumen 'cerrado' (StatementAlreadyClosedError) o 'pagado'
  (StatementAlreadyPaidError) — dos excepciones distintas para dos estados
  distintos, probadas por separado.

fees_by_month()/fees_projection() NO se prueban acá con el mismo detalle
porque no migraron (siguen siendo reportes agregados directos, ver nota en
services/fees_service.py) — sí se confirma que siguen funcionando sin
romperse.

Correlo con:
    python verify/compras_cuotas/verify_fees_service.py
"""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from verify._dummy_db import crear_dummy_db
from db.database import DatabaseManager
from repositories.cuentas_repository import CuentasRepository
from services.fees_service import (
    FeesService,
    FeesError,
    PurchaseNotFoundError,
    StatementNotFoundError,
    FeeNotFoundError,
    StatementAlreadyPaidError,
    StatementAlreadyClosedError,
    FeeAlreadyConfirmedError,
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
    cuentas_repo = CuentasRepository(manager)
    svc = FeesService(manager)

    cuenta_a = cuentas_repo.crear(nombre="Tarjeta Fees A", tipo="credito", moneda_codigo="ARS")
    cat_egreso = manager.fetchone("SELECT id FROM categorias WHERE tipo = 'egreso' LIMIT 1;")["id"]

    print("--- create_purchase() — camino feliz ---")
    res_compra = svc.create_purchase(
        date_str="2026-01-05",
        concept="Lavarropas",
        account_id=cuenta_a,
        category_id=cat_egreso,
        currency_code="ARS",
        total_amount=3000.0,
        total_fees=3,
    )
    caso("create_purchase() devuelve success=True", True, res_compra.success)
    caso("create_purchase() devuelve amount_per_fee correcto", 1000.0, res_compra.data["amount_per_fee"])
    compra_id = res_compra.entity_id

    print("\n--- create_purchase() — validaciones de negocio intactas ---")
    caso_excepcion(
        "create_purchase() con total_amount<=0 sigue lanzando FeesError",
        FeesError,
        lambda: svc.create_purchase(
            date_str="2026-01-05", concept="X", account_id=cuenta_a, category_id=cat_egreso,
            currency_code="ARS", total_amount=0.0, total_fees=3,
        ),
    )
    caso_excepcion(
        "create_purchase() con total_fees<1 sigue lanzando FeesError",
        FeesError,
        lambda: svc.create_purchase(
            date_str="2026-01-05", concept="X", account_id=cuenta_a, category_id=cat_egreso,
            currency_code="ARS", total_amount=100.0, total_fees=0,
        ),
    )
    caso_excepcion(
        "create_purchase() con concept vacío sigue lanzando FeesError",
        FeesError,
        lambda: svc.create_purchase(
            date_str="2026-01-05", concept="   ", account_id=cuenta_a, category_id=cat_egreso,
            currency_code="ARS", total_amount=100.0, total_fees=3,
        ),
    )
    caso_excepcion(
        "create_purchase() con currency_code inexistente sigue lanzando ValueError",
        ValueError,
        lambda: svc.create_purchase(
            date_str="2026-01-05", concept="X", account_id=cuenta_a, category_id=cat_egreso,
            currency_code="XYZ", total_amount=100.0, total_fees=3,
        ),
    )

    print("\n--- get_purchase() / list_purchases() — shape enriquecido intacto ---")
    fila_compra = svc.get_purchase(compra_id)
    caso("get_purchase() trae account_name vía JOIN", "Tarjeta Fees A", fila_compra["account_name"] if fila_compra else None)
    caso("get_purchase() trae currency_symbol vía JOIN", "$", fila_compra["currency_symbol"] if fila_compra else None)
    caso("get_purchase() de un id inexistente devuelve None", None, svc.get_purchase(999999))

    listado_compras = svc.list_purchases(account_id=cuenta_a)
    ids_listado = [r["id"] for r in listado_compras]
    caso("list_purchases(account_id=...) incluye la compra creada", True, compra_id in ids_listado)
    fila_lista = next((r for r in listado_compras if r["id"] == compra_id), None)
    caso("list_purchases() trae currency_code enriquecido", True, "currency_code" in fila_lista.keys() if fila_lista else False)
    caso("list_purchases() NO trae currency_symbol (asimetría real preservada)", False, "currency_symbol" in fila_lista.keys() if fila_lista else True)

    print("\n--- get_fees_for_purchase() ---")
    cuotas_compra = svc.get_fees_for_purchase(compra_id)
    caso("get_fees_for_purchase() devuelve las 3 cuotas generadas", 3, len(cuotas_compra))
    caso("get_fees_for_purchase() ordena por numero_cuota ASC", [1, 2, 3], [c["numero_cuota"] for c in cuotas_compra])
    caso("get_fees_for_purchase() calcula monto_cuota_minor correcto", 100000, cuotas_compra[0]["monto_cuota_minor"])

    caso_excepcion(
        "get_fees_for_purchase() sobre id inexistente sigue lanzando PurchaseNotFoundError",
        PurchaseNotFoundError,
        lambda: svc.get_fees_for_purchase(999999),
    )

    print("\n--- open_statement() — idempotente ---")
    res_stmt_1 = svc.open_statement(account_id=cuenta_a, month=1, year=2026, tax_percentage_bp=500)
    caso("open_statement() primera vez: already_existed=False", False, res_stmt_1.data["already_existed"])
    stmt_id = res_stmt_1.entity_id

    res_stmt_2 = svc.open_statement(account_id=cuenta_a, month=1, year=2026, tax_percentage_bp=999)
    caso("open_statement() segunda vez: already_existed=True (idempotente)", True, res_stmt_2.data["already_existed"])
    caso("open_statement() segunda vez: devuelve el mismo statement_id", stmt_id, res_stmt_2.entity_id)

    caso_excepcion(
        "open_statement() con month fuera de rango sigue lanzando ValueError",
        ValueError,
        lambda: svc.open_statement(account_id=cuenta_a, month=13, year=2026),
    )
    caso_excepcion(
        "open_statement() con account_id inexistente sigue lanzando FeesError",
        FeesError,
        lambda: svc.open_statement(account_id=999999, month=2, year=2026),
    )

    print("\n--- confirm_fee() — validaciones de negocio intactas ---")
    fee_1, fee_2, fee_3 = [c["id"] for c in cuotas_compra]

    caso_excepcion(
        "confirm_fee() sobre fee_id inexistente sigue lanzando FeeNotFoundError",
        FeeNotFoundError,
        lambda: svc.confirm_fee(fee_id=999999, statement_id=stmt_id),
    )
    caso_excepcion(
        "confirm_fee() sobre statement_id inexistente sigue lanzando StatementNotFoundError",
        StatementNotFoundError,
        lambda: svc.confirm_fee(fee_id=fee_1, statement_id=999999),
    )

    res_confirm_1 = svc.confirm_fee(fee_id=fee_1, statement_id=stmt_id)
    caso("confirm_fee() primera vez devuelve success=True", True, res_confirm_1.success)

    caso_excepcion(
        "confirm_fee() sobre una fee ya confirmada sigue lanzando FeeAlreadyConfirmedError",
        FeeAlreadyConfirmedError,
        lambda: svc.confirm_fee(fee_id=fee_1, statement_id=stmt_id),
    )

    svc.confirm_fee(fee_id=fee_2, statement_id=stmt_id)
    svc.confirm_fee(fee_id=fee_3, statement_id=stmt_id)

    print("\n--- add_extra_charge() — validaciones de negocio ---")
    caso_excepcion(
        "add_extra_charge() sobre statement_id inexistente sigue lanzando StatementNotFoundError",
        StatementNotFoundError,
        lambda: svc.add_extra_charge(999999, concept="X", charge_type="impuesto", amount_minor=100),
    )
    caso_excepcion(
        "add_extra_charge() con concept vacío lanza FeesError",
        FeesError,
        lambda: svc.add_extra_charge(stmt_id, concept="   ", charge_type="impuesto", amount_minor=100),
    )
    caso_excepcion(
        "add_extra_charge() con charge_type inválido lanza ValueError",
        ValueError,
        lambda: svc.add_extra_charge(stmt_id, concept="X", charge_type="descuento", amount_minor=100),
    )

    print("\n--- add_extra_charge() / list_extra_charges() — camino feliz, incluye un ajuste negativo ---")
    res_cargo_1 = svc.add_extra_charge(stmt_id, concept="IVA", charge_type="impuesto", amount_minor=25000)
    caso("add_extra_charge() devuelve success=True", True, res_cargo_1.success)
    cargo_1_id = res_cargo_1.entity_id

    svc.add_extra_charge(stmt_id, concept="Impuesto sellos", charge_type="impuesto", amount_minor=10000)
    svc.add_extra_charge(stmt_id, concept="Ajuste a favor", charge_type="ajuste", amount_minor=-5000)
    res_cargo_a_eliminar = svc.add_extra_charge(stmt_id, concept="Cargo cargado por error", charge_type="otro", amount_minor=999)

    cargos_listados = svc.list_extra_charges(stmt_id)
    caso("list_extra_charges() devuelve los 4 cargos agregados", 4, len(cargos_listados))
    caso("list_extra_charges() trae el monto negativo del ajuste", True, any(c["monto_minor"] == -5000 for c in cargos_listados))

    caso_excepcion(
        "list_extra_charges() sobre statement_id inexistente sigue lanzando StatementNotFoundError",
        StatementNotFoundError,
        lambda: svc.list_extra_charges(999999),
    )

    fila_stmt_abierto = svc.get_statement(stmt_id)
    caso(
        "add_extra_charge() sobre un resumen 'abierto' NO toca monto_impuestos_minor (recién close_statement() consolida)",
        0,
        fila_stmt_abierto["monto_impuestos_minor"],
    )
    caso(
        "add_extra_charge() sobre un resumen 'abierto' NO toca porcentaje_impuesto_bp",
        500,
        fila_stmt_abierto["porcentaje_impuesto_bp"],
    )

    print("\n--- remove_extra_charge() ---")
    caso_excepcion(
        "remove_extra_charge() sobre statement_id inexistente sigue lanzando StatementNotFoundError",
        StatementNotFoundError,
        lambda: svc.remove_extra_charge(res_cargo_a_eliminar.entity_id, 999999),
    )

    res_stmt_otro = svc.open_statement(account_id=cuenta_a, month=3, year=2027)
    caso_excepcion(
        "remove_extra_charge() con un charge_id que pertenece a OTRO resumen lanza FeesError (validación de pertenencia)",
        FeesError,
        lambda: svc.remove_extra_charge(res_cargo_a_eliminar.entity_id, res_stmt_otro.entity_id),
    )

    res_remove = svc.remove_extra_charge(res_cargo_a_eliminar.entity_id, stmt_id)
    caso("remove_extra_charge() devuelve success=True", True, res_remove.success)

    cargos_tras_eliminar = svc.list_extra_charges(stmt_id)
    caso("remove_extra_charge() ya no aparece en list_extra_charges()", False, res_cargo_a_eliminar.entity_id in [c["id"] for c in cargos_tras_eliminar])
    caso("list_extra_charges() ahora devuelve 3 cargos", 3, len(cargos_tras_eliminar))

    fila_stmt_abierto_2 = svc.get_statement(stmt_id)
    caso(
        "remove_extra_charge() sobre un resumen 'abierto' tampoco toca monto_impuestos_minor",
        0,
        fila_stmt_abierto_2["monto_impuestos_minor"],
    )

    print("\n--- close_statement() — CARGOS EXTRA DE RESUMEN: consolida totales reales ---")
    # Mismo escenario numérico que verify_resumenes_tarjeta_repository.py:
    # 3 cuotas de 100000 = 300000 de consumos, los 3 cargos que quedaron
    # (25000 + 10000 - 5000, el cargo de 999 ya se eliminó) netean a 30000
    # de impuestos, bp derivado = 1000.
    res_close = svc.close_statement(stmt_id)
    caso("close_statement() devuelve success=True", True, res_close.success)
    caso("close_statement() devuelve monto_consumos_minor consolidado (3 cuotas confirmadas)", 300000, res_close.data["monto_consumos_minor"])
    caso("close_statement() devuelve monto_impuestos_minor consolidado (neto con ajuste negativo, sin el cargo eliminado)", 30000, res_close.data["monto_impuestos_minor"])
    caso("close_statement() devuelve porcentaje_impuesto_bp derivado", 1000, res_close.data["porcentaje_impuesto_bp"])

    fila_stmt_cerrado = svc.get_statement(stmt_id)
    caso("close_statement() persiste estado='cerrado'", "cerrado", fila_stmt_cerrado["estado"])
    caso("close_statement() persiste monto_consumos_minor", 300000, fila_stmt_cerrado["monto_consumos_minor"])
    caso("close_statement() persiste monto_impuestos_minor", 30000, fila_stmt_cerrado["monto_impuestos_minor"])
    caso("close_statement() persiste porcentaje_impuesto_bp", 1000, fila_stmt_cerrado["porcentaje_impuesto_bp"])

    caso_excepcion(
        "close_statement() sobre statement_id inexistente sigue lanzando StatementNotFoundError",
        StatementNotFoundError,
        lambda: svc.close_statement(999999),
    )

    print("\n--- add_extra_charge()/remove_extra_charge() sobre un resumen 'cerrado' (no pagado) ---")
    caso_excepcion(
        "add_extra_charge() sobre un resumen 'cerrado' lanza StatementAlreadyClosedError (NO StatementAlreadyPaidError)",
        StatementAlreadyClosedError,
        lambda: svc.add_extra_charge(stmt_id, concept="Tarde", charge_type="otro", amount_minor=1),
    )
    caso_excepcion(
        "remove_extra_charge() sobre un resumen 'cerrado' lanza StatementAlreadyClosedError (NO StatementAlreadyPaidError)",
        StatementAlreadyClosedError,
        lambda: svc.remove_extra_charge(cargo_1_id, stmt_id),
    )

    print("\n--- pay_statement() — CARGOS EXTRA DE RESUMEN: ahora requiere y persiste monto_pagado_minor ---")
    res_pay = svc.pay_statement(stmt_id, payment_date="2026-01-20", monto_pagado_minor=330000)
    caso("pay_statement() devuelve success=True", True, res_pay.success)
    caso("pay_statement() devuelve total_paid = monto_pagado_minor pasado", 330000, res_pay.data["total_paid"])

    fila_stmt_pagado = svc.get_statement(stmt_id)
    caso("pay_statement() deja estado='pagado'", "pagado", fila_stmt_pagado["estado"])
    caso("pay_statement() persiste monto_total_pagado_minor = monto_pagado_minor pasado", 330000, fila_stmt_pagado["monto_total_pagado_minor"])

    cuotas_tras_pago = svc.get_fees_for_purchase(compra_id)
    caso("pay_statement() marca las 3 cuotas confirmadas como 'pagado'", ["pagado", "pagado", "pagado"], [c["estado"] for c in cuotas_tras_pago])

    print("\n--- pay_statement()/close_statement()/confirm_fee() sobre un resumen ya pagado ---")
    caso_excepcion(
        "pay_statement() sobre un resumen ya pagado sigue lanzando StatementAlreadyPaidError",
        StatementAlreadyPaidError,
        lambda: svc.pay_statement(stmt_id, payment_date="2026-01-21", monto_pagado_minor=1),
    )
    caso_excepcion(
        "close_statement() sobre un resumen ya pagado sigue lanzando StatementAlreadyPaidError",
        StatementAlreadyPaidError,
        lambda: svc.close_statement(stmt_id),
    )
    caso_excepcion(
        "add_extra_charge() sobre un resumen 'pagado' lanza StatementAlreadyPaidError (NO StatementAlreadyClosedError)",
        StatementAlreadyPaidError,
        lambda: svc.add_extra_charge(stmt_id, concept="Tarde", charge_type="otro", amount_minor=1),
    )
    caso_excepcion(
        "remove_extra_charge() sobre un resumen 'pagado' lanza StatementAlreadyPaidError (NO StatementAlreadyClosedError)",
        StatementAlreadyPaidError,
        lambda: svc.remove_extra_charge(cargo_1_id, stmt_id),
    )

    # Escenario nuevo para confirm_fee() sobre resumen pagado: otra compra,
    # otro resumen, se paga sin confirmar la única cuota.
    res_compra_2 = svc.create_purchase(
        date_str="2026-02-05", concept="Otra compra", account_id=cuenta_a,
        category_id=cat_egreso, currency_code="ARS", total_amount=1000.0, total_fees=1,
    )
    res_stmt_3 = svc.open_statement(account_id=cuenta_a, month=2, year=2026)
    fee_suelta = svc.get_fees_for_purchase(res_compra_2.entity_id)[0]["id"]
    svc.pay_statement(res_stmt_3.entity_id, payment_date="2026-02-10", monto_pagado_minor=0)
    caso_excepcion(
        "confirm_fee() sobre un resumen ya pagado sigue lanzando StatementAlreadyPaidError",
        StatementAlreadyPaidError,
        lambda: svc.confirm_fee(fee_id=fee_suelta, statement_id=res_stmt_3.entity_id),
    )

    print("\n--- get_statement() / list_statements() — shape enriquecido intacto ---")
    caso("get_statement() de un id inexistente devuelve None", None, svc.get_statement(999999))
    listado_stmts = svc.list_statements(account_id=cuenta_a, year=2026)
    ids_stmts = [r["id"] for r in listado_stmts]
    caso("list_statements(account_id=..., year=...) incluye el resumen pagado", True, stmt_id in ids_stmts)
    fila_stmt_lista = next((r for r in listado_stmts if r["id"] == stmt_id), None)
    caso("list_statements() trae account_name enriquecido", True, "account_name" in fila_stmt_lista.keys() if fila_stmt_lista else False)

    print("\n--- fees_by_month()/fees_projection() — siguen funcionando (no migrados) ---")
    filas_mes = svc.fees_by_month(month=2, year=2026, estado="en_resumen")
    caso("fees_by_month() sigue devolviendo una lista", True, isinstance(filas_mes, list))
    proyeccion = svc.fees_projection(months=2, currency_code="ARS")
    caso("fees_projection() sigue devolviendo 2 meses", 2, len(proyeccion))

    print("\n--- cancel_purchase() ---")
    res_compra_3 = svc.create_purchase(
        date_str="2026-03-05", concept="Compra a cancelar", account_id=cuenta_a,
        category_id=cat_egreso, currency_code="ARS", total_amount=900.0, total_fees=3,
    )
    compra_3_id = res_compra_3.entity_id

    res_cancel = svc.cancel_purchase(compra_3_id, notes="Me arrepentí")
    caso("cancel_purchase() devuelve success=True", True, res_cancel.success)
    caso("cancel_purchase() cancela las 3 cuotas pendientes", 3, res_cancel.data["fees_cancelled"])

    fila_compra_3 = svc.get_purchase(compra_3_id)
    caso("cancel_purchase() deja estado='cancelada'", "cancelada", fila_compra_3["estado"])
    cuotas_compra_3 = svc.get_fees_for_purchase(compra_3_id)
    caso("cancel_purchase() marca las cuotas como 'omitido'", ["omitido", "omitido", "omitido"], [c["estado"] for c in cuotas_compra_3])

    caso_excepcion(
        "cancel_purchase() sobre una compra ya cancelada sigue lanzando FeesError",
        FeesError,
        lambda: svc.cancel_purchase(compra_3_id),
    )
    caso_excepcion(
        "cancel_purchase() sobre id inexistente sigue lanzando PurchaseNotFoundError",
        PurchaseNotFoundError,
        lambda: svc.cancel_purchase(999999),
    )

    manager.desconectar()

    print(f"\n--- Resumen: {casos_ok}/{casos_total} casos OK ---")


if __name__ == "__main__":
    main()
