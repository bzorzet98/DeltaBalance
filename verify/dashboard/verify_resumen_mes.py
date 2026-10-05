"""
verify/dashboard/verify_resumen_mes.py

Verifica DashboardService.get_resumen_mes() (pantalla DASHBOARD,
ui/screens/resumen_mes.py; docs/DATA_MODEL_DECISIONS.md sección 34) con un
escenario de octubre 2026 inspirado en el pedido (BRUNO y NOELIA comparten
un hogar):

1. Ingresos estimados vs. cobrados del mes.
2. Egresos fijos: estimado, pagado y lo que FALTA pagar (informativo).
3. Cuotas por tarjeta.
4. Gastos del Registro por categoría con su estimado: sin PAGO TARJETA ni
   AUTOTRANSFERENCIA (CATEGORIAS_EXCLUIDAS_GASTOS), un compartido que pagó
   BRUNO cuenta solo su parte, una categoría con presupuesto y sin gasto
   aparece en 0, nada de otro mes ni de otra moneda; VIVIENDA (donde se
   paga el alquiler) marcada como categoría de fijos (CATEGORIAS_DE_FIJOS).
5. Gastos compartidos: la parte de BRUNO de lo que pagó NOELIA en el mes.
6. Deudas ACUMULADAS hasta el 31/10: informales por persona (un saldo
   negativo pasa al otro lado), compartidos pendientes (lo que pagó BRUNO
   se lo debe NOELIA; lo que pagó NOELIA se lo debe BRUNO; descuenta pagos
   parciales; sin los saldados ni los posteriores al corte).
7. Balance en sus dos modos (switch ESTIMADO / REAL de la pantalla):
   ESTIMADO = ingresos estimados − fijos estimados − cuotas − variables
   estimados + neto de deudas; REAL = cobrados − fijos pagados − cuotas −
   variables reales + neto de deudas. Variables: GASTOS DEL MES sin las
   categorías de fijos; los gastos compartidos no entran. En ESTIMADO cada
   categoría resta lo gastado + lo que falta de su presupuesto (sin
   presupuesto, lo gastado). Y los totales de cada tarjeta.
7b. Calculadora de escenarios: el detalle por ítem (ingresos y fijos por
   concepto, cuotas por tarjeta, variables por categoría), que en cada modo
   suma exacto el DISPONIBLE, y DashboardService.calcular_escenario():
   excluir ítems o un grupo entero, ajuste manual, ids ajenos ignorados.
7c. ESTIMADO con una categoría pasada de su presupuesto (diciembre): resta
   lo gastado, no el presupuesto.
8. Otra moneda (USD) y sin usuario local; errores.

Correlo con:
    python verify/dashboard/verify_resumen_mes.py
"""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from verify._dummy_db import crear_dummy_db
from db.database import DatabaseManager
from repositories.cuentas_repository import CuentasRepository
from repositories.gastos_compartidos_repository import GastosCompartidosRepository
from repositories.transacciones_repository import TransaccionesRepository
from services.dashboard_service import CATEGORIAS_DE_FIJOS, CATEGORIAS_EXCLUIDAS_GASTOS, MODOS_BALANCE, DashboardService
from services.debts_service import DebtsService
from services.fees_service import FeesService
from services.ingresos_service import IngresosService
from services.presupuestos_service import PresupuestosService
from services.shared_expenses_service import SharedExpensesService

MES, ANIO = 10, 2026


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
        except tipo_esperado as e:
            casos_ok += 1
            print(f"✅ {descripcion} — lanzó {tipo_esperado.__name__}: {e}")
        except Exception as e:
            print(f"❌ {descripcion} — esperaba {tipo_esperado.__name__}, se lanzó {type(e).__name__}: {e!r}")

    db_path = crear_dummy_db()
    print(f"Dummy DB creada en: {db_path}\n")

    manager = DatabaseManager(db_path=db_path)
    manager.inicializar()
    dashboard = DashboardService(manager)
    ars = manager.fetchone("SELECT id FROM monedas WHERE codigo = 'ARS';")["id"]
    usd = manager.fetchone("SELECT id FROM monedas WHERE codigo = 'USD';")["id"]

    def categoria(subcategoria: str) -> str:
        return manager.fetchone("SELECT id FROM categorias WHERE subcategoria = ?;", (subcategoria,))["id"]

    cuentas = CuentasRepository(manager)
    banco = cuentas.crear(nombre="BANCO", tipo="debito", moneda_codigo="ARS")
    tarjeta = cuentas.crear(nombre="CREDICOOP CABAL", tipo="credito", moneda_codigo="ARS")
    transacciones = TransaccionesRepository(manager)

    def egreso(fecha: str, subcategoria: str, monto_minor: int, moneda_id: int = ars) -> str:
        return transacciones.crear(
            fecha=fecha, concepto=f"{subcategoria} {fecha}", cuenta_id=banco, categoria_id=categoria(subcategoria),
            moneda_id=moneda_id, tipo_movimiento="egreso", monto_minor=monto_minor,
        )

    # --- Escenario ---
    ingresos = IngresosService(manager)
    ingresos.create("SUELDO", 129546000, 121739300, ars, MES, ANIO)
    ingresos.create("FREELANCE", 50000, 50000, usd, MES, ANIO)

    presupuestos = PresupuestosService(manager)
    presupuestos.create_fijo("ALQUILER", 18000000, ars, MES, ANIO, monto_real_minor=17850000)
    presupuestos.create_fijo("SEGURO", 3000000, ars, MES, ANIO, monto_real_minor=3500000)  # pagó de más
    presupuestos.create_variable(categoria("SUPERMERCADO"), 8000000, ars, MES, ANIO)
    presupuestos.create_variable(categoria("TRANSPORTE / AUTO"), 5000000, ars, MES, ANIO)

    FeesService(manager).create_purchase(
        date_str="2026-10-05", concept="TV", account_id=tarjeta, category_id=categoria("HOGAR"),
        currency_code="ARS", total_amount=145230.0, total_fees=1, first_fee_month=MES, first_fee_year=ANIO,
    )

    egreso("2026-10-03", "SUPERMERCADO", 6532000)
    tx_compartida = egreso("2026-10-04", "SUPERMERCADO", 2000000)  # BRUNO la comparte 50/50
    egreso("2026-10-06", "OCIO", 3200000)
    egreso("2026-10-01", "VIVIENDA", 17850000)                     # el alquiler, pagado desde el Registro
    egreso("2026-10-15", "PAGO TARJETA", 10000000)                 # excluida: ya está en CUOTAS
    egreso("2026-10-16", "AUTOTRANSFERENCIA", 5000000)             # excluida: no es un gasto
    egreso("2026-09-30", "SUPERMERCADO", 100000)                   # otro mes
    egreso("2026-10-07", "SUPERMERCADO", 1000, moneda_id=usd)      # otra moneda

    compartidos = SharedExpensesService(manager)
    gastos_repo = GastosCompartidosRepository(manager)
    hogar = compartidos.create_hogar(nombre_creador_local="BRUNO", nombre_hogar="CASA")
    compartidos.join_hogar(hogar.data["codigo_invitacion"], "NOELIA")

    def compartido(pagador: str, origen_id: str, subcategoria: str, base_minor: int, fecha: str) -> str:
        return compartidos.add_shared_expense(
            hogar_id=hogar.entity_id, pagador=pagador, origen_tipo="transaccion", origen_id=origen_id,
            categoria_id=categoria(subcategoria), monto_base_minor=base_minor, coeficiente_deuda=50.0, fecha=fecha,
        ).entity_id

    compartido("BRUNO", tx_compartida, "SUPERMERCADO", 2000000, "2026-10-04")       # NOELIA le debe 10.000
    compartido("NOELIA", "tx-noelia-1", "HOGAR", 5700000, "2026-10-10")             # BRUNO le debe 28.500
    parcial = compartido("NOELIA", "tx-noelia-2", "ALIMENTOS", 3000000, "2026-10-12")  # 15.000, pagó 10.000
    gastos_repo.actualizar_monto_pendiente(parcial, 500000, "pendiente")
    compartido("NOELIA", "tx-noelia-3", "OCIO", 2000000, "2026-09-15")              # septiembre, pendiente
    saldado = compartido("BRUNO", "tx-bruno-viejo", "HOGAR", 4000000, "2026-08-01")
    gastos_repo.marcar_saldado(saldado)
    compartido("NOELIA", "tx-noelia-4", "OCIO", 9000000, "2026-11-02")              # después del corte

    deudas = DebtsService(manager)
    deudas.create("N. RIVERA", "PRÉSTAMO", "me_deben", 47131200, ars, "2026-09-01")
    deudas.create("JUAN", "PRÉSTAMO", "me_deben", 1000000, ars, "2026-11-05")      # después del corte
    deudas.create("PEDRO", "PRÉSTAMO", "me_deben", 500000, ars, "2026-07-01")
    deudas.create("PEDRO", "DEVOLVIÓ DE MÁS", "me_deben", -700000, ars, "2026-08-01")
    deudas.create("N. RIVERA", "PRÉSTAMO", "debo", 47060000, ars, "2026-10-01")
    deudas.create("PAPI", "PRÉSTAMO", "debo", 4358500, ars, "2026-08-01")

    r = dashboard.get_resumen_mes(MES, ANIO, usuario_local="bruno")

    print("--- 1. Ingresos ---")
    caso("ingresos ARS: estimado y cobrado", (129546000, 121739300),
         (r["ingresos"]["estimado_minor"], r["ingresos"]["real_minor"]))
    caso("el resumen es en ARS y hay datos también en USD", ("ARS", ["ARS", "USD"]),
         (r["moneda_codigo"], r["monedas_disponibles"]))

    print("\n--- 2. Egresos fijos ---")
    fijos = r["egresos_fijos"]
    caso("estimado 180.000 + 30.000", 21000000, fijos["estimado_minor"])
    caso("pagado 178.500 + 35.000", 21350000, fijos["real_minor"])
    caso("falta pagar: 1.500 del alquiler (el seguro, pagado de más, no suma negativo)", 150000, fijos["pendiente_minor"])

    print("\n--- 3. Cuotas por tarjeta ---")
    caso("CREDICOOP CABAL: la cuota de octubre", [("CREDICOOP CABAL", 14523000)],
         [(c["cuenta"], c["monto_minor"]) for c in r["cuotas"]])

    print("\n--- 4. Gastos del Registro ---")
    caso("categorías excluidas: autotransferencia y pago de tarjeta",
         {("MOVIMIENTO CAPITAL", "AUTOTRANSFERENCIA"), ("TARJETA DE CRÉDITO", "PAGO TARJETA")},
         set(CATEGORIAS_EXCLUIDAS_GASTOS))
    caso("por categoría (real, estimado), la mayor primero",
         [
             ("VIVIENDA", 17850000, None),
             ("SUPERMERCADO", 7532000, 8000000),       # 65.320 + 10.000 (su parte del compartido)
             ("OCIO", 3200000, None),
             ("TRANSPORTE / AUTO", 0, 5000000),        # presupuesto sin gasto
         ],
         [(g["categoria"], g["real_minor"], g["estimado_minor"]) for g in r["gastos_registro"]])
    caso("categorías de fijos: vivienda, servicios básicos y seguros",
         {("EGRESOS", "VIVIENDA"), ("EGRESOS", "SERVICIOS BÁSICOS"), ("EGRESOS", "SEGUROS")},
         set(CATEGORIAS_DE_FIJOS))
    caso("solo VIVIENDA marcada como categoría de fijos",
         [("VIVIENDA", True), ("SUPERMERCADO", False), ("OCIO", False), ("TRANSPORTE / AUTO", False)],
         [(g["categoria"], g["categoria_de_fijos"]) for g in r["gastos_registro"]])

    print("\n--- 5. Gastos compartidos (la parte de BRUNO de lo que pagó NOELIA en octubre) ---")
    caso("HOGAR 28.500 y ALIMENTOS 15.000 (el de septiembre y el de noviembre, no)",
         [("HOGAR", 2850000), ("ALIMENTOS", 1500000)],
         [(g["categoria"], g["monto_minor"]) for g in r["gastos_compartidos"]])

    print("\n--- 6. Deudas acumuladas al 31/10 ---")
    d = r["deudas"]
    caso("fecha de corte: el último día del mes", "2026-10-31", r["fecha_corte"])
    caso("ME DEBEN: N. RIVERA (informal) y NOELIA (compartidos: lo que pagó BRUNO)",
         [("N. RIVERA", "informal", 47131200), ("NOELIA", "compartidos", 1000000)],
         [(x["persona"], x["tipo"], x["monto_minor"]) for x in d["me_deben"]])
    caso("DEBO: N. RIVERA, NOELIA (28.500 + 5.000 pendiente + 10.000 de septiembre), PAPI y PEDRO (le debe lo que devolvió de más)",
         [
             ("N. RIVERA", "informal", 47060000),
             ("NOELIA", "compartidos", 4350000),
             ("PAPI", "informal", 4358500),
             ("PEDRO", "informal", 200000),
         ],
         [(x["persona"], x["tipo"], x["monto_minor"]) for x in d["debo"]])
    caso("subtotales y neto", (48131200, 55968500, -7837300),
         (d["subtotal_me_deben_minor"], d["subtotal_debo_minor"], d["neto_minor"]))

    print("\n--- 7. Balance en sus dos modos ---")
    def componentes(modo: str) -> tuple:
        b = r["balance"][modo]
        return (b["ingresos_minor"], b["egresos_fijos_minor"], b["cuotas_minor"],
                b["gastos_variables_minor"], b["neto_deudas_minor"])

    caso("el balance trae los dos modos", ["estimado", "real"], list(r["balance"]))
    caso("los modos son los del service", list(MODOS_BALANCE), list(r["balance"]))
    caso("totales de las tarjetas: cuotas, gastos del mes (VIVIENDA incluida) y compartidos",
         (14523000, 28582000, 4350000),
         (r["totales"]["cuotas_minor"], r["totales"]["gastos_registro_minor"], r["totales"]["gastos_compartidos_minor"]))
    # ESTIMADO: variables = gastado + lo que falta del presupuesto — SUPERMERCADO 75.320 + 4.680 = 80.000,
    # TRANSPORTE / AUTO 0 + 50.000 = 50.000 — y OCIO, sin presupuesto, lo gastado: 32.000.
    caso("ESTIMADO: ingresos, fijos estimados, cuotas, variables estimados, neto de deudas",
         (129546000, 21000000, 14523000, 16200000, -7837300), componentes("estimado"))
    caso("ESTIMADO: DISPONIBLE = 1.295.460 − 210.000 − 145.230 − 162.000 − 78.373",
         69985700, r["balance"]["estimado"]["disponible_minor"])
    # REAL: variables = SUPERMERCADO 75.320 + OCIO 32.000 + TRANSPORTE 0; VIVIENDA (fijo) y compartidos, no.
    caso("REAL: cobrados, fijos pagados, cuotas, variables reales, neto de deudas",
         (121739300, 21350000, 14523000, 10732000, -7837300), componentes("real"))
    caso("REAL: DISPONIBLE = 1.217.393 − 213.500 − 145.230 − 107.320 − 78.373",
         67297000, r["balance"]["real"]["disponible_minor"])

    print("\n--- 7b. Detalle por ítem y calculadora de escenarios ---")
    caso("ingresos por concepto (el de USD no)", [("SUELDO", 129546000, 121739300)],
         [(i["concepto"], i["estimado_minor"], i["real_minor"]) for i in r["ingresos"]["items"]])
    caso("fijos por concepto (estimado, pagado, falta pagar)",
         [("ALQUILER", 18000000, 17850000, 150000), ("SEGURO", 3000000, 3500000, 0)],
         [(f["concepto"], f["estimado_minor"], f["real_minor"], f["pendiente_minor"]) for f in r["egresos_fijos"]["items"]])
    caso("cuotas con el id de su tarjeta", [tarjeta], [c["cuenta_id"] for c in r["cuotas"]])
    for modo in MODOS_BALANCE:
        balance_modo = r["balance"][modo]
        caso(f"{modo.upper()}: los grupos, en el orden del DISPONIBLE",
             ["ingresos", "egresos_fijos", "cuotas", "gastos_variables", "neto_deudas"],
             [g["clave"] for g in balance_modo["grupos"]])
        caso(f"{modo.upper()}: la suma de todos los aportes es el DISPONIBLE", balance_modo["disponible_minor"],
             sum(i["aporte_minor"] for g in balance_modo["grupos"] for i in g["items"]))
    variables = next(g for g in r["balance"]["estimado"]["grupos"] if g["clave"] == "gastos_variables")
    caso("ESTIMADO: los variables sin VIVIENDA (fijo), en negativo; OCIO, sin presupuesto, con lo gastado (no 0)",
         [(f"variable:{categoria('SUPERMERCADO')}", -8000000), (f"variable:{categoria('OCIO')}", -3200000),
          (f"variable:{categoria('TRANSPORTE / AUTO')}", -5000000)],
         [(i["id"], i["aporte_minor"]) for i in variables["items"]])

    estimado, real = r["balance"]["estimado"], r["balance"]["real"]
    id_cuota = f"cuota:{tarjeta}"
    id_super = f"variable:{categoria('SUPERMERCADO')}"
    id_sueldo = f"ingreso:{r['ingresos']['items'][0]['id']}"
    sin_nada = dashboard.calcular_escenario(estimado, [], 0)
    caso("sin excluir nada ni ajuste: el DISPONIBLE de siempre, no es escenario", (69985700, False),
         (sin_nada["disponible_minor"], sin_nada["es_escenario"]))
    sin_cuota = dashboard.calcular_escenario(estimado, [id_cuota], 0)
    caso("sin la cuota de CREDICOOP: +145.230, el grupo CUOTAS en 0", (84508700, 0, True),
         (sin_cuota["disponible_minor"], sin_cuota["grupos"]["cuotas"], sin_cuota["es_escenario"]))
    sin_cuota_ni_super = dashboard.calcular_escenario(estimado, [id_cuota, id_super], 0)
    caso("y sin SUPERMERCADO: +80.000; los variables quedan en OCIO + TRANSPORTE / AUTO", (92508700, -8200000),
         (sin_cuota_ni_super["disponible_minor"], sin_cuota_ni_super["grupos"]["gastos_variables"]))
    con_ajuste = dashboard.calcular_escenario(estimado, [id_cuota, id_super], -30000000)
    caso("con un ajuste de −300.000", 62508700, con_ajuste["disponible_minor"])
    caso("sin el grupo INGRESOS entero (su único ítem)", -59560300,
         dashboard.calcular_escenario(estimado, [id_sueldo], 0)["disponible_minor"])
    caso("REAL: el mismo id excluye SUPERMERCADO también ahí (+75.320)", 74829000,
         dashboard.calcular_escenario(real, [id_super], 0)["disponible_minor"])
    ajeno = dashboard.calcular_escenario(estimado, ["variable:no-existe"], 0)
    caso("un id que no es de este balance se ignora: no es escenario", (69985700, False),
         (ajeno["disponible_minor"], ajeno["es_escenario"]))
    caso("solo el ajuste ya es escenario", (70085700, True),
         tuple(dashboard.calcular_escenario(estimado, [], 100000)[k] for k in ("disponible_minor", "es_escenario")))

    print("\n--- 7c. ESTIMADO con una categoría pasada de su presupuesto (diciembre) ---")
    # Mes aparte (los presupuestos no se copian solos entre meses): OCIO con presupuesto 10.000 y 25.000 gastados.
    presupuestos.create_variable(categoria("OCIO"), 1000000, ars, 12, ANIO)
    egreso("2026-12-05", "OCIO", 2500000)
    r_dic = dashboard.get_resumen_mes(12, ANIO, usuario_local="bruno")
    caso("OCIO en diciembre: (real, presupuesto)", [("OCIO", 2500000, 1000000)],
         [(g["categoria"], g["real_minor"], g["estimado_minor"]) for g in r_dic["gastos_registro"]])
    caso("se pasó: ESTIMADO resta lo gastado (25.000), no el presupuesto (10.000); REAL también 25.000",
         (2500000, 2500000),
         (r_dic["balance"]["estimado"]["gastos_variables_minor"], r_dic["balance"]["real"]["gastos_variables_minor"]))

    print("\n--- 8. Otra moneda, sin usuario local y errores ---")
    r_usd = dashboard.get_resumen_mes(MES, ANIO, usuario_local="BRUNO", moneda_codigo="USD")
    caso("USD: solo lo de USD (ingreso 500 y el gasto de 10, sin presupuesto en USD)",
         (50000, [("SUPERMERCADO", 1000, None)], []),
         (r_usd["ingresos"]["estimado_minor"],
          [(g["categoria"], g["real_minor"], g["estimado_minor"]) for g in r_usd["gastos_registro"]],
          r_usd["cuotas"]))
    r_sin = dashboard.get_resumen_mes(MES, ANIO)
    caso("sin usuario local: lo avisa y no hay compartidos", (True, []),
         (r_sin["sin_usuario_local"], r_sin["gastos_compartidos"]))
    caso("sin usuario local: DEUDAS solo informales",
         (["informal"], ["informal", "informal", "informal"]),
         ([x["tipo"] for x in r_sin["deudas"]["me_deben"]], [x["tipo"] for x in r_sin["deudas"]["debo"]]))
    caso_excepcion("moneda desconocida → ValueError", ValueError,
                   lambda: dashboard.get_resumen_mes(MES, ANIO, moneda_codigo="XYZ"))
    caso_excepcion("mes 13 → ValueError", ValueError, lambda: dashboard.get_resumen_mes(13, ANIO))

    print(f"\n{casos_ok}/{casos_total} casos OK")
    print(f"(La DB temporal quedó en {db_path} — no es data/deltabalance.db.)")


if __name__ == "__main__":
    main()
