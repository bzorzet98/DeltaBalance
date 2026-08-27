"""
verify/hogares_gastos_compartidos/verify_add_shared_purchase.py

Verifica SharedExpensesService.add_shared_purchase() — método agregado en la
ronda de "Compras en cuotas compartidas" para orquestar compartir una compra
en cuotas completa (compras_cuotas + cuotas_credito) según su modo_deuda.
Confirmado por lectura de shared_expenses_service.py y fees_service.py antes
de escribirlo: esta orquestación NO existía (modo_deuda/monto_reintegro_minor
estaban en el schema desde hace tiempo, pero sin ningún método que los usara).

Cubre:
- modo_deuda='total_unico': un solo gasto compartido, con el reintegro
  descontado del total antes de aplicar el coeficiente.
- modo_deuda='total_unico' duplicado: GastoCompartidoDuplicadoError (es una
  única operación, no un lote — se aborta, no se saltea).
- modo_deuda='prorrateado': un gasto compartido por cada cuotas_credito de
  la compra, con el reintegro prorrateado entre cuotas antes de aplicar el
  coeficiente, y fecha = primer día del mes/año proyectado de cada cuota.
- modo_deuda='prorrateado' con duplicado PARCIAL (algunas cuotas ya
  compartidas, otras no): las ya compartidas se SALTEAN, las demás se crean
  igual — no se aborta el lote completo por una sola cuota ya cubierta.
- Validaciones: HogarNotFoundError, MiembroNotFoundError, CompraNotFoundError,
  ValueError de coeficiente_deuda fuera de rango, ValueError de compra
  prorrateada sin ninguna cuota generada.

Correlo con:
    python verify/hogares_gastos_compartidos/verify_add_shared_purchase.py
"""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from verify._dummy_db import crear_dummy_db
from db.database import DatabaseManager
from repositories.cuentas_repository import CuentasRepository
from repositories.compras_cuotas_repository import ComprasCuotasRepository
from repositories.gastos_compartidos_repository import GastosCompartidosRepository
from services.fees_service import FeesService
from services.shared_expenses_service import (
    SharedExpensesService,
    HogarNotFoundError,
    MiembroNotFoundError,
    CompraNotFoundError,
    GastoCompartidoDuplicadoError,
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
    compras_repo = ComprasCuotasRepository(manager)
    gastos_repo = GastosCompartidosRepository(manager)
    fees_svc = FeesService(manager)
    shared_svc = SharedExpensesService(manager)

    cuenta_credito = cuentas_repo.crear(nombre="Visa Verify", tipo="credito", moneda_codigo="ARS")
    cat_egreso = manager.fetchone("SELECT id FROM categorias WHERE tipo = 'egreso' LIMIT 1;")["id"]

    hogar = shared_svc.create_hogar(nombre_creador_local="bruno", nombre_hogar="Hogar Compras").entity_id

    # ============================================================
    # modo_deuda='total_unico'
    # ============================================================
    print("--- add_shared_purchase() — modo_deuda='total_unico' ---")
    compra_total_unico = fees_svc.create_purchase(
        date_str="2026-01-05", concept="Heladera", account_id=cuenta_credito, category_id=cat_egreso,
        currency_code="ARS", total_amount=3000.0, total_fees=3,
    ).entity_id
    compras_repo.actualizar(compra_total_unico, modo_deuda="total_unico", monto_reintegro_minor=30000)

    resultado_tu = shared_svc.add_shared_purchase(
        compra_id=compra_total_unico, hogar_id=hogar, pagador="bruno", coeficiente_deuda=50.0,
    )
    caso("total_unico: success=True", True, resultado_tu.success)
    caso("total_unico: gastos_creados=1", 1, resultado_tu.data["gastos_creados"])
    caso("total_unico: crea exactamente 1 gasto_id", 1, len(resultado_tu.data["gasto_ids"]))

    gasto_tu = gastos_repo.obtener_por_id(resultado_tu.data["gasto_ids"][0])
    caso("total_unico: origen_tipo='compra_cuotas'", "compra_cuotas", gasto_tu["origen_tipo"])
    caso("total_unico: origen_id=compra_id", compra_total_unico, gasto_tu["origen_id"])
    caso(
        "total_unico: monto_base_minor = 300000 (total) - 30000 (reintegro) = 270000",
        270000,
        gasto_tu["monto_base_minor"],
    )
    caso(
        "total_unico: monto_adeudado_minor = round(270000*50/100) = 135000",
        135000,
        gasto_tu["monto_adeudado_minor"],
    )
    caso("total_unico: categoria_id heredado de la compra", cat_egreso, gasto_tu["categoria_id"])
    caso("total_unico: fecha = fecha_compra de la compra", "2026-01-05", gasto_tu["fecha"])

    caso_excepcion(
        "total_unico duplicado: segunda llamada sobre la misma compra lanza GastoCompartidoDuplicadoError",
        GastoCompartidoDuplicadoError,
        lambda: shared_svc.add_shared_purchase(
            compra_id=compra_total_unico, hogar_id=hogar, pagador="bruno", coeficiente_deuda=50.0,
        ),
    )

    # ============================================================
    # modo_deuda='prorrateado' (default de columna, no se setea explícito)
    # ============================================================
    print("\n--- add_shared_purchase() — modo_deuda='prorrateado' ---")
    compra_prorrateada = fees_svc.create_purchase(
        date_str="2026-02-10", concept="Notebook", account_id=cuenta_credito, category_id=cat_egreso,
        currency_code="ARS", total_amount=3000.0, total_fees=3,
    ).entity_id
    compras_repo.actualizar(compra_prorrateada, monto_reintegro_minor=30000)  # 10000 por cuota

    resultado_pr = shared_svc.add_shared_purchase(
        compra_id=compra_prorrateada, hogar_id=hogar, pagador="bruno", coeficiente_deuda=50.0,
    )
    caso("prorrateado: success=True", True, resultado_pr.success)
    caso("prorrateado: gastos_creados=3 (una por cuota)", 3, resultado_pr.data["gastos_creados"])
    caso("prorrateado: cuotas_ya_compartidas vacío (nada duplicado)", [], resultado_pr.data["cuotas_ya_compartidas"])

    gastos_pr = [gastos_repo.obtener_por_id(gid) for gid in resultado_pr.data["gasto_ids"]]
    caso(
        "prorrateado: las 3 filas tienen origen_tipo='cuota_credito'",
        True,
        all(g["origen_tipo"] == "cuota_credito" for g in gastos_pr),
    )
    caso(
        "prorrateado: monto_base_minor por cuota = 100000 (cuota) - 10000 (reintegro/3) = 90000",
        True,
        all(g["monto_base_minor"] == 90000 for g in gastos_pr),
    )
    caso(
        "prorrateado: monto_adeudado_minor por cuota = round(90000*50/100) = 45000",
        True,
        all(g["monto_adeudado_minor"] == 45000 for g in gastos_pr),
    )
    fechas_pr = sorted(g["fecha"] for g in gastos_pr)
    caso(
        "prorrateado: fechas = primer día de cada mes proyectado (feb/mar/abr 2026)",
        ["2026-02-01", "2026-03-01", "2026-04-01"],
        fechas_pr,
    )

    # ============================================================
    # modo_deuda='prorrateado' con duplicado PARCIAL
    # ============================================================
    print("\n--- add_shared_purchase() — prorrateado con duplicado parcial ---")
    compra_parcial = fees_svc.create_purchase(
        date_str="2026-03-01", concept="TV", account_id=cuenta_credito, category_id=cat_egreso,
        currency_code="ARS", total_amount=3000.0, total_fees=3,
    ).entity_id
    cuotas_parcial = fees_svc.get_fees_for_purchase(compra_parcial)
    cuota_ya_compartida_id = cuotas_parcial[0]["id"]

    # Simula que esa cuota puntual ya se había compartido antes (ej. suelta,
    # antes de que existiera add_shared_purchase()) — mismo shape que crearía
    # este propio método, para que el chequeo de duplicado la detecte igual.
    gastos_repo.crear(
        hogar_id=hogar, pagador="bruno", origen_tipo="cuota_credito", origen_id=cuota_ya_compartida_id,
        categoria_id=cat_egreso, monto_base_minor=100000, coeficiente_deuda=50.0,
        monto_adeudado_minor=50000, fecha="2026-03-01",
    )

    resultado_parcial = shared_svc.add_shared_purchase(
        compra_id=compra_parcial, hogar_id=hogar, pagador="bruno", coeficiente_deuda=50.0,
    )
    caso("duplicado parcial: gastos_creados=2 (la ya compartida se saltea)", 2, resultado_parcial.data["gastos_creados"])
    caso(
        "duplicado parcial: cuotas_ya_compartidas trae la cuota preexistente",
        [cuota_ya_compartida_id],
        resultado_parcial.data["cuotas_ya_compartidas"],
    )
    caso(
        "duplicado parcial: no se duplicó el gasto de la cuota ya compartida",
        1,
        len(gastos_repo.listar_por_origen("cuota_credito", cuota_ya_compartida_id)),
    )

    # ============================================================
    # Validaciones
    # ============================================================
    print("\n--- add_shared_purchase() — validaciones ---")
    caso_excepcion(
        "hogar_id inexistente lanza HogarNotFoundError",
        HogarNotFoundError,
        lambda: shared_svc.add_shared_purchase(
            compra_id=compra_prorrateada, hogar_id=999999, pagador="bruno", coeficiente_deuda=50.0,
        ),
    )
    caso_excepcion(
        "pagador que no es miembro lanza MiembroNotFoundError",
        MiembroNotFoundError,
        lambda: shared_svc.add_shared_purchase(
            compra_id=compra_prorrateada, hogar_id=hogar, pagador="no_es_miembro", coeficiente_deuda=50.0,
        ),
    )
    caso_excepcion(
        "compra_id inexistente lanza CompraNotFoundError",
        CompraNotFoundError,
        lambda: shared_svc.add_shared_purchase(
            compra_id=999999, hogar_id=hogar, pagador="bruno", coeficiente_deuda=50.0,
        ),
    )
    caso_excepcion(
        "coeficiente_deuda=0 lanza ValueError (debe ser > 0)",
        ValueError,
        lambda: shared_svc.add_shared_purchase(
            compra_id=compra_prorrateada, hogar_id=hogar, pagador="bruno", coeficiente_deuda=0,
        ),
    )
    caso_excepcion(
        "coeficiente_deuda=150 lanza ValueError (debe ser <= 100)",
        ValueError,
        lambda: shared_svc.add_shared_purchase(
            compra_id=compra_prorrateada, hogar_id=hogar, pagador="bruno", coeficiente_deuda=150.0,
        ),
    )

    compra_sin_cuotas = compras_repo.crear(
        fecha_compra="2026-04-01", concepto="Sin cuotas", cuenta_id=cuenta_credito, categoria_id=cat_egreso,
        moneda_id=manager.fetchone("SELECT id FROM monedas WHERE codigo = 'ARS';")["id"],
        monto_total_minor=100000, total_cuotas=0, monto_por_cuota_minor=0,
    )
    caso_excepcion(
        "compra prorrateada sin ninguna cuota generada lanza ValueError",
        ValueError,
        lambda: shared_svc.add_shared_purchase(
            compra_id=compra_sin_cuotas, hogar_id=hogar, pagador="bruno", coeficiente_deuda=50.0,
        ),
    )

    manager.desconectar()

    print(f"\n--- Resumen: {casos_ok}/{casos_total} casos OK ---")


if __name__ == "__main__":
    main()
