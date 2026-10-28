"""
verify/dashboard/verify_resumen_mes.py

Verifica DashboardService.get_resumen_mes() (pantalla DASHBOARD,
ui/screens/resumen_mes.py; docs/DATA_MODEL_DECISIONS.md secciones 34 y 36)
con un escenario de octubre 2026 (BRUNO y NOELIA comparten un hogar). La
regla central del rediseño: el ESTIMADO sale de las pantallas Ingresos y
Presupuestos, el REAL solo del Registro.

1. Ingresos fijos: el estimado de cada concepto de Ingresos (lo "cobrado"
   que se carga ahí NO se usa) y el real del Registro en SUELDO / BECA.
2. Ingresos variables: todo otro ingreso del Registro — también
   AHORRO/INVERSIÓN (una venta) y una devolución en SUPERMERCADO —, uno por
   tag (sin distinguir mayúsculas ni espacios); los sin tag, uno por
   categoría. Afuera: AUTOTRANSFERENCIA, DEUDA, otro mes, otra moneda.
3. Egresos fijos: el estimado de los presupuestos 'fijo' (su "pagado" NO se
   usa) y el real del Registro en VIVIENDA / SERVICIOS BÁSICOS (con su
   presupuesto variable como estimado).
4. Egresos variables con presupuesto (una categoría pasada proyecta lo
   gastado) y sin presupuesto (estimado = real), con AHORRO/INVERSIÓN. Un
   compartido que pagó BRUNO cuenta solo su parte. Afuera: PAGO TARJETA,
   AUTOTRANSFERENCIA, CAMBIO MONEDA, DEUDA.
5. Tarjeta: a pagar por tarjeta, pagos realizados, total y falta pagar.
6. Movimientos de capital: AUTOTRANSFERENCIA y CAMBIO MONEDA informativas;
   DEUDA cuenta (ingreso suma, egreso resta).
7. Deudas ACUMULADAS al 31/10: informales y compartidos, por persona.
8. DISPONIBLE HOY vs. PROYECTADO: la tarjeta resta su TOTAL en los dos;
   prestar / que te presten por el Registro no lo mueve.
9. Calculadora de escenarios.
10. Gráfico: las dos tortas suman lo mismo que el REAL de la pantalla.
11. Otra moneda (USD), sin usuario local, tarjeta pagada de más
    (noviembre: falta pagar nunca negativo) y errores.

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
from services.dashboard_service import (
    CATEGORIAS_AHORRO,
    CATEGORIAS_DE_FIJOS,
    CATEGORIAS_DEUDA,
    CATEGORIAS_INGRESOS_FIJOS,
    CATEGORIAS_NEUTRAS,
    MODOS_DISPONIBLE,
    ORIGEN_ESTIMADO,
    ORIGEN_REGISTRO,
    DashboardService,
)
from services.deudas_service import DebtsService
from services.compras_cuotas_service import FeesService
from services.ingresos_service import IngresosService
from services.presupuestos_service import PresupuestosService
from services.gastos_compartidos_service import SharedExpensesService

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
    cabal = cuentas.crear(nombre="CREDICOOP CABAL", tipo="credito", moneda_codigo="ARS")
    nacion = cuentas.crear(nombre="NACION MASTERCARD", tipo="credito", moneda_codigo="ARS")
    transacciones = TransaccionesRepository(manager)

    def movimiento(tipo: str, fecha: str, subcategoria: str, monto_minor: int, tag=None, moneda_id: int = ars) -> str:
        # El tipo va en el concepto: una entrada y una salida iguales el mismo día no son un duplicado.
        return transacciones.crear(
            fecha=fecha, concepto=f"{tipo.upper()} {subcategoria} {fecha}", cuenta_id=banco,
            categoria_id=categoria(subcategoria), moneda_id=moneda_id, tipo_movimiento=tipo,
            monto_minor=monto_minor, tag=tag,
        )

    def egreso(fecha: str, subcategoria: str, monto_minor: int, moneda_id: int = ars) -> str:
        return movimiento("egreso", fecha, subcategoria, monto_minor, moneda_id=moneda_id)

    def ingreso(fecha: str, subcategoria: str, monto_minor: int, tag=None, moneda_id: int = ars) -> str:
        return movimiento("ingreso", fecha, subcategoria, monto_minor, tag=tag, moneda_id=moneda_id)

    deudas_svc = DebtsService(manager)

    # --- Escenario: ingresos ---
    ingresos_svc = IngresosService(manager)
    ingresos_svc.create("BECA DOCTORAL", 121739300, 100000000, ars, MES, ANIO)  # "cobrado" a mano: no se usa
    ingresos_svc.create("CARGO JTP", 17684600, 0, ars, MES, ANIO)
    ingresos_svc.create("FREELANCE", 50000, 50000, usd, MES, ANIO)

    ingreso("2026-10-01", "SUELDO / BECA", 121739300)                   # el REAL de los ingresos fijos
    ingreso("2026-10-05", "COBRO DEUDA", 2900000, "Cobro deuda Noe")
    ingreso("2026-10-08", "INGRESO VARIABLE", 100000, "regalo papá")
    ingreso("2026-10-20", "INGRESO VARIABLE", 50000, " REGALO PAPÁ ")    # el mismo tag, escrito distinto
    ingreso("2026-10-12", "REINTEGRO", 30000)                            # sin tag: uno por categoría
    ingreso("2026-10-19", "AHORRO/INVERSIÓN", 2000000, "venta cedears")  # entró plata: ingreso variable
    ingreso("2026-10-21", "SUPERMERCADO", 20000)                         # una devolución: es un ingreso
    ingreso("2026-10-16", "AUTOTRANSFERENCIA", 5000000)                  # neutra
    ingreso("2026-10-22", "DEUDA", 1000000)                              # te prestaron: capital, cuenta
    deudas_svc.create("MARTA", "PRÉSTAMO", "debo", 1000000, ars, "2026-10-22")   # ...y su fila en Deudas
    ingreso("2026-09-30", "INGRESO VARIABLE", 70000, "OTRO MES")         # no: otro mes
    ingreso("2026-10-09", "INGRESO VARIABLE", 1000, "CLASES", moneda_id=usd)    # no en ARS: otra moneda
    ingreso("2026-10-17", "CAMBIO MONEDA", 70000, moneda_id=usd)         # la otra punta del cambio, en USD

    # --- Escenario: egresos ---
    presupuestos = PresupuestosService(manager)
    presupuestos.create_fijo("ALQUILER", 15000000, ars, MES, ANIO, monto_real_minor=15000000)  # "pagado": no se usa
    presupuestos.create_fijo("SEGURO AUTO", 6237400, ars, MES, ANIO)
    presupuestos.create_fijo("GAS", 8000000, ars, MES, ANIO)
    presupuestos.create_variable(categoria("SUPERMERCADO"), 8000000, ars, MES, ANIO)
    presupuestos.create_variable(categoria("GASTRONOMÍA"), 4000000, ars, MES, ANIO)
    presupuestos.create_variable(categoria("TRANSPORTE / AUTO"), 5000000, ars, MES, ANIO)
    presupuestos.create_variable(categoria("OCIO"), 1000000, ars, MES, ANIO)                # se va a pasar
    presupuestos.create_variable(categoria("SERVICIOS BÁSICOS"), 9000000, ars, MES, ANIO)   # categoría de fijos

    egreso("2026-10-03", "SUPERMERCADO", 4000000)
    tx_compartida = egreso("2026-10-04", "SUPERMERCADO", 2000000)  # BRUNO la comparte 50/50: cuenta 10.000
    egreso("2026-10-06", "GASTRONOMÍA", 1600000)
    egreso("2026-10-07", "TRANSPORTE / AUTO", 4800000)
    egreso("2026-10-10", "OCIO", 2500000)                          # presupuesto 10.000: se pasó
    egreso("2026-10-11", "MASCOTAS", 982100)                       # sin presupuesto
    egreso("2026-10-12", "REGALOS", 500000)                        # sin presupuesto
    egreso("2026-10-18", "AHORRO/INVERSIÓN", 3000000)              # salió plata: egreso variable
    egreso("2026-10-01", "VIVIENDA", 14500000)                     # categoría de fijos: el REAL de los fijos
    egreso("2026-10-09", "SERVICIOS BÁSICOS", 7000000)             # ídem
    egreso("2026-10-15", "PAGO TARJETA", 14523000)                 # pago de la CABAL: va a TARJETA
    egreso("2026-09-30", "PAGO TARJETA", 1000000)                  # otro mes
    egreso("2026-10-16", "AUTOTRANSFERENCIA", 5000000)             # neutra: con la entrada, suma cero
    egreso("2026-10-17", "CAMBIO MONEDA", 1000000)                 # neutra
    egreso("2026-10-14", "DEUDA", 3000000)                         # le prestaste a LUCAS: capital, cuenta
    deudas_svc.create("LUCAS", "PRÉSTAMO", "me_deben", 3000000, ars, "2026-10-14")   # ...y su fila en Deudas
    egreso("2026-09-30", "SUPERMERCADO", 100000)                   # otro mes
    egreso("2026-10-07", "SUPERMERCADO", 1000, moneda_id=usd)      # otra moneda

    # --- Escenario: tarjetas ---
    fees = FeesService(manager)
    fees.create_purchase(
        date_str="2026-10-05", concept="TV", account_id=cabal, category_id=categoria("HOGAR"),
        currency_code="ARS", total_amount=145230.0, total_fees=1, first_fee_month=MES, first_fee_year=ANIO,
    )
    fees.create_purchase(
        date_str="2026-10-06", concept="HELADERA", account_id=nacion, category_id=categoria("HOGAR"),
        currency_code="ARS", total_amount=30000.0, total_fees=1, first_fee_month=MES, first_fee_year=ANIO,
    )

    # --- Escenario: compartidos y deudas ---
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
    compartido("NOELIA", "tx-noelia-3", "HOGAR", 2000000, "2026-09-15")             # septiembre, pendiente
    saldado = compartido("BRUNO", "tx-bruno-viejo", "HOGAR", 4000000, "2026-08-01")
    gastos_repo.marcar_saldado(saldado)
    compartido("NOELIA", "tx-noelia-4", "HOGAR", 9000000, "2026-11-02")             # después del corte

    deudas_svc.create("N. RIVERA", "PRÉSTAMO", "me_deben", 47131200, ars, "2026-09-01")
    deudas_svc.create("JUAN", "PRÉSTAMO", "me_deben", 1000000, ars, "2026-11-05")      # después del corte
    deudas_svc.create("PEDRO", "PRÉSTAMO", "me_deben", 500000, ars, "2026-07-01")
    deudas_svc.create("PEDRO", "DEVOLVIÓ DE MÁS", "me_deben", -700000, ars, "2026-08-01")
    deudas_svc.create("N. RIVERA", "PRÉSTAMO", "debo", 47060000, ars, "2026-10-01")
    deudas_svc.create("PAPI", "PRÉSTAMO", "debo", 4358500, ars, "2026-08-01")

    r = dashboard.get_resumen_mes(MES, ANIO, usuario_local="bruno")
    caso("el resumen es en ARS y hay datos también en USD", ("ARS", ["ARS", "USD"]),
         (r["moneda_codigo"], r["monedas_disponibles"]))

    print("\n--- 0. Qué hace cada categoría ---")
    caso("neutras: AUTOTRANSFERENCIA y CAMBIO MONEDA",
         {("MOVIMIENTO CAPITAL", "AUTOTRANSFERENCIA"), ("MOVIMIENTO CAPITAL", "CAMBIO MONEDA")},
         set(CATEGORIAS_NEUTRAS))
    caso("ahorro (cuenta según su tipo_movimiento): AHORRO/INVERSIÓN e INVERSIONES",
         {("MOVIMIENTO CAPITAL", "AHORRO/INVERSIÓN"), ("MOVIMIENTO CAPITAL", "INVERSIONES")},
         set(CATEGORIAS_AHORRO))
    caso("deuda (cuenta, en movimientos de capital): DEUDA", {("MOVIMIENTO CAPITAL", "DEUDA")}, set(CATEGORIAS_DEUDA))
    caso("real de los ingresos fijos: SUELDO / BECA", {("INGRESOS", "SUELDO / BECA")}, set(CATEGORIAS_INGRESOS_FIJOS))
    caso("real de los egresos fijos: VIVIENDA, SERVICIOS BÁSICOS y SEGUROS",
         {("EGRESOS", "VIVIENDA"), ("EGRESOS", "SERVICIOS BÁSICOS"), ("EGRESOS", "SEGUROS")},
         set(CATEGORIAS_DE_FIJOS))

    print("\n--- 1. Ingresos fijos (estimado: pantalla Ingresos; real: el Registro) ---")
    ing = r["ingresos"]
    caso("cada concepto de Ingresos, solo con su estimado (el cobrado de 1.000.000 no se usa) — el de USD no",
         [("BECA DOCTORAL", ORIGEN_ESTIMADO, 121739300, 0), ("CARGO JTP", ORIGEN_ESTIMADO, 17684600, 0)],
         sorted((i["concepto"], i["origen"], i["estimado_minor"], i["real_minor"])
                for i in ing["fijos"] if i["origen"] == ORIGEN_ESTIMADO))
    caso("el real: SUELDO / BECA del Registro, en su propia fila (id, concepto, origen, estimado, real)",
         [(f"ingreso_registro:{categoria('SUELDO / BECA')}", "SUELDO / BECA", ORIGEN_REGISTRO, 0, 121739300)],
         [(i["id"], i["concepto"], i["origen"], i["estimado_minor"], i["real_minor"])
          for i in ing["fijos"] if i["origen"] == ORIGEN_REGISTRO])
    caso("subtotal fijos: 1.217.393 + 176.846 estimado, 1.217.393 real (del Registro)",
         {"estimado_minor": 139423900, "real_minor": 121739300}, ing["subtotal_fijos"])

    print("\n--- 2. Ingresos variables ---")
    caso("uno por tag, el mayor primero (id, tag, concepto, estimado, real); ahorro y devolución incluidos",
         [
             ("ingreso_variable:COBRO DEUDA NOE", "COBRO DEUDA NOE", "COBRO DEUDA NOE", 0, 2900000),
             ("ingreso_variable:VENTA CEDEARS", "VENTA CEDEARS", "VENTA CEDEARS", 0, 2000000),
             ("ingreso_variable:REGALO PAPÁ", "REGALO PAPÁ", "REGALO PAPÁ", 0, 150000),
             (f"ingreso_sin_tag:{categoria('REINTEGRO')}", None, "REINTEGRO (SIN TAG)", 0, 30000),
             (f"ingreso_sin_tag:{categoria('SUPERMERCADO')}", None, "SUPERMERCADO (SIN TAG)", 0, 20000),
         ],
         [(i["id"], i["tag"], i["concepto"], i["estimado_minor"], i["real_minor"]) for i in ing["variables"]])
    caso("subtotal variables: solo real (29.000 + 20.000 + 1.500 + 300 + 200)",
         {"estimado_minor": 0, "real_minor": 5100000}, ing["subtotal_variables"])
    caso("total ingresos: el estimado no suma los variables", {"estimado_minor": 139423900, "real_minor": 126839300},
         ing["total"])

    print("\n--- 3. Egresos fijos (estimado: presupuestos; real: el Registro) ---")
    eg = r["egresos"]
    caso("cada presupuesto fijo, solo con su estimado (el pagado de ALQUILER no se usa)",
         [("ALQUILER", ORIGEN_ESTIMADO, 15000000, 0), ("GAS", ORIGEN_ESTIMADO, 8000000, 0),
          ("SEGURO AUTO", ORIGEN_ESTIMADO, 6237400, 0)],
         sorted((f["concepto"], f["origen"], f["estimado_minor"], f["real_minor"])
                for f in eg["fijos"] if f["origen"] == ORIGEN_ESTIMADO))
    caso("el real: las categorías de fijos del Registro, la mayor primero; SERVICIOS BÁSICOS con su presupuesto",
         [
             (f"fijo_registro:{categoria('VIVIENDA')}", "VIVIENDA", 0, 14500000),
             (f"fijo_registro:{categoria('SERVICIOS BÁSICOS')}", "SERVICIOS BÁSICOS", 9000000, 7000000),
         ],
         [(f["id"], f["concepto"], f["estimado_minor"], f["real_minor"])
          for f in eg["fijos"] if f["origen"] == ORIGEN_REGISTRO])
    caso("subtotal fijos: 150.000 + 80.000 + 62.374 + 90.000 estimado; 145.000 + 70.000 real",
         {"estimado_minor": 38237400, "real_minor": 21500000}, eg["subtotal_fijos"])

    print("\n--- 4. Egresos variables ---")
    caso("con presupuesto (categoría, estimado = presupuesto, proyectado, real), el mayor real primero",
         [
             ("SUPERMERCADO", 8000000, 8000000, 5000000),           # 40.000 + 10.000 (su parte); la devolución no resta
             ("TRANSPORTE / AUTO", 5000000, 5000000, 4800000),
             ("OCIO", 1000000, 2500000, 2500000),                   # se pasó: proyecta lo gastado
             ("GASTRONOMÍA", 4000000, 4000000, 1600000),
         ],
         [(v["categoria"], v["estimado_minor"], v["proyectado_minor"], v["real_minor"])
          for v in eg["variables_con_presupuesto"]])
    caso("sin presupuesto: estimado = proyectado = real; AHORRO/INVERSIÓN incluido",
         [("AHORRO/INVERSIÓN", 3000000, 3000000, 3000000), ("MASCOTAS", 982100, 982100, 982100),
          ("REGALOS", 500000, 500000, 500000)],
         [(v["categoria"], v["estimado_minor"], v["proyectado_minor"], v["real_minor"])
          for v in eg["variables_sin_presupuesto"]])
    caso("subtotal variables: el estimado suma los proyectados (OCIO con lo gastado)",
         {"estimado_minor": 23982100, "real_minor": 18382100}, eg["subtotal_variables"])
    caso("total egresos", {"estimado_minor": 62219500, "real_minor": 39882100}, eg["total"])

    print("\n--- 5. Tarjeta ---")
    t = r["tarjeta"]
    caso("a pagar por tarjeta (id, cuenta, monto)",
         [(f"cuota:{cabal}", "CREDICOOP CABAL", 14523000), (f"cuota:{nacion}", "NACION MASTERCARD", 3000000)],
         [(c["id"], c["cuenta"], c["a_pagar_minor"]) for c in t["por_cuenta"]])
    caso("pagos realizados: el PAGO TARJETA de octubre (el de septiembre no)", 14523000, t["pagos_realizados_minor"])
    caso("total a pagar y falta pagar", (17523000, 3000000), (t["total_a_pagar_minor"], t["falta_pagar_minor"]))

    print("\n--- 6. Movimientos de capital ---")
    capital = r["movimientos_capital"]
    caso("por categoría (id, categoría, neto, cuenta): neutras informativas, DEUDA cuenta",
         [
             (f"neutro:{categoria('AUTOTRANSFERENCIA')}", "AUTOTRANSFERENCIA", 0, False),   # +50.000 − 50.000
             (f"neutro:{categoria('CAMBIO MONEDA')}", "CAMBIO MONEDA", -1000000, False),
             (f"capital:{categoria('DEUDA')}", "DEUDA", -2000000, True),                     # +10.000 − 30.000
         ],
         [(c["id"], c["categoria"], c["neto_minor"], c["cuenta"]) for c in capital["items"]])
    caso("lo que cuenta: solo DEUDA", -2000000, capital["neto_que_cuenta_minor"])

    print("\n--- 7. Deudas acumuladas al 31/10 ---")
    d = r["deudas"]
    caso("fecha de corte: el último día del mes", "2026-10-31", r["fecha_corte"])
    caso("ME DEBEN: LUCAS y N. RIVERA (informal), NOELIA (compartidos: lo que pagó BRUNO)",
         [("LUCAS", "informal", 3000000), ("N. RIVERA", "informal", 47131200), ("NOELIA", "compartidos", 1000000)],
         [(x["persona"], x["tipo"], x["monto_minor"]) for x in d["me_deben"]])
    caso("DEBO: MARTA, N. RIVERA, NOELIA (28.500 + 5.000 pendiente + 10.000 de septiembre), PAPI y PEDRO",
         [
             ("MARTA", "informal", 1000000),
             ("N. RIVERA", "informal", 47060000),
             ("NOELIA", "compartidos", 4350000),
             ("PAPI", "informal", 4358500),
             ("PEDRO", "informal", 200000),
         ],
         [(x["persona"], x["tipo"], x["monto_minor"]) for x in d["debo"]])
    caso("subtotales y neto", (51131200, 56968500, -5837300),
         (d["subtotal_me_deben_minor"], d["subtotal_debo_minor"], d["neto_minor"]))

    print("\n--- 8. DISPONIBLE ---")
    disponible = r["disponible"]
    caso("HOY = 1.268.393 − 398.821 − 175.230 (tarjeta, el total) − 20.000 (DEUDA) − 58.373 (deudas)",
         61596900, disponible["hoy_minor"])
    caso("PROYECTADO = 1.394.239 + 51.000 − 382.374 − 239.821 − 175.230 − 20.000 − 58.373",
         56944100, disponible["proyectado_minor"])
    caso("prestar y que te presten por el Registro no mueve el DISPONIBLE: DEUDA −20.000, deudas +30.000 − 10.000",
         0, capital["neto_que_cuenta_minor"] + (3000000 - 1000000))

    print("\n--- 9. Calculadora de escenarios ---")
    caso("los modos del DISPONIBLE", ["hoy", "proyectado"], list(MODOS_DISPONIBLE))
    for modo in MODOS_DISPONIBLE:
        grupos = disponible["grupos"][modo]
        caso(f"{modo.upper()}: los grupos, en el orden de la pantalla",
             ["ingresos_fijos", "ingresos_variables", "egresos_fijos", "egresos_variables", "tarjeta", "capital",
              "neto_deudas"],
             [g["clave"] for g in grupos])
        caso(f"{modo.upper()}: la suma de todos los aportes es el DISPONIBLE", disponible[f"{modo}_minor"],
             sum(i["aporte_minor"] for g in grupos for i in g["items"]))
    hoy, proyectado = disponible["grupos"]["hoy"], disponible["grupos"]["proyectado"]
    id_cabal = f"cuota:{cabal}"
    id_ocio = f"variable:{categoria('OCIO')}"
    id_sueldo = f"ingreso_registro:{categoria('SUELDO / BECA')}"
    sin_nada = dashboard.calcular_escenario(hoy, [], 0)
    caso("sin excluir nada ni ajuste: el DISPONIBLE de siempre, no es escenario", (61596900, False),
         (sin_nada["disponible_minor"], sin_nada["es_escenario"]))
    sin_cabal = dashboard.calcular_escenario(hoy, [id_cabal], 0)
    caso("HOY sin la CABAL: +145.230; el grupo tarjeta queda en la NACION", (76119900, -3000000, True),
         (sin_cabal["disponible_minor"], sin_cabal["grupos"]["tarjeta"], sin_cabal["es_escenario"]))
    caso("HOY sin la CABAL ni OCIO: +25.000 más (lo gastado)", 78619900,
         dashboard.calcular_escenario(hoy, [id_cabal, id_ocio], 0)["disponible_minor"])
    caso("PROYECTADO sin la CABAL ni OCIO: +145.230 + 25.000 (su proyectado)", 73967100,
         dashboard.calcular_escenario(proyectado, [id_cabal, id_ocio], 0)["disponible_minor"])
    caso("con un ajuste de −300.000", 48619900,
         dashboard.calcular_escenario(hoy, [id_cabal, id_ocio], -30000000)["disponible_minor"])
    caso("sin NETO DEUDAS: +58.373", 67434200, dashboard.calcular_escenario(hoy, ["neto_deudas"], 0)["disponible_minor"])
    caso("sin la DEUDA de movimientos de capital: +20.000", 63596900,
         dashboard.calcular_escenario(hoy, [f"capital:{categoria('DEUDA')}"], 0)["disponible_minor"])
    caso("sin un ingreso variable (REGALO PAPÁ): −1.500", 61446900,
         dashboard.calcular_escenario(hoy, ["ingreso_variable:REGALO PAPÁ"], 0)["disponible_minor"])
    caso("HOY sin el SUELDO / BECA del Registro: −1.217.393", -60142400,
         dashboard.calcular_escenario(hoy, [id_sueldo], 0)["disponible_minor"])
    caso("PROYECTADO sin el SUELDO / BECA del Registro: no cambia (ahí cuentan los estimados), pero es escenario",
         (56944100, True),
         tuple(dashboard.calcular_escenario(proyectado, [id_sueldo], 0)[k] for k in ("disponible_minor", "es_escenario")))
    ajeno = dashboard.calcular_escenario(hoy, ["variable:no-existe"], 0)
    caso("un id que no es de estos grupos se ignora: no es escenario", (61596900, False),
         (ajeno["disponible_minor"], ajeno["es_escenario"]))
    caso("solo el ajuste ya es escenario", (61696900, True),
         tuple(dashboard.calcular_escenario(hoy, [], 100000)[k] for k in ("disponible_minor", "es_escenario")))

    print("\n--- 10. Gráfico (las dos tortas) ---")
    grafico = r["grafico"]
    caso("torta INGRESOS: por categoría, la mayor primero (sin AUTOTRANSFERENCIA ni DEUDA)",
         [("SUELDO / BECA", 121739300), ("COBRO DEUDA", 2900000), ("AHORRO/INVERSIÓN", 2000000),
          ("INGRESO VARIABLE", 150000), ("REINTEGRO", 30000), ("SUPERMERCADO", 20000)],
         [(p["categoria"], p["monto_minor"]) for p in grafico["ingresos"]])
    caso("torta INGRESOS: suma el REAL de los ingresos", ing["total"]["real_minor"],
         sum(p["monto_minor"] for p in grafico["ingresos"]))
    caso("torta EGRESOS: por categoría + la tarjeta (su total), sin PAGO TARJETA, CAMBIO MONEDA ni DEUDA",
         [("TARJETA DE CRÉDITO", 17523000), ("VIVIENDA", 14500000), ("SERVICIOS BÁSICOS", 7000000),
          ("SUPERMERCADO", 5000000), ("TRANSPORTE / AUTO", 4800000), ("AHORRO/INVERSIÓN", 3000000),
          ("OCIO", 2500000), ("GASTRONOMÍA", 1600000), ("MASCOTAS", 982100), ("REGALOS", 500000)],
         [(p["categoria"], p["monto_minor"]) for p in grafico["egresos"]])
    caso("torta EGRESOS: suma el REAL de los egresos + el total de la tarjeta",
         eg["total"]["real_minor"] + t["total_a_pagar_minor"], sum(p["monto_minor"] for p in grafico["egresos"]))

    print("\n--- 11. Otra moneda, sin usuario local, tarjeta pagada de más y errores ---")
    r_usd = dashboard.get_resumen_mes(MES, ANIO, moneda="USD", usuario_local="BRUNO")
    caso("USD: ingreso fijo (solo estimado), ingreso variable, gasto sin presupuesto (el presupuesto es en ARS), "
         "sin tarjetas, y la otra punta del CAMBIO MONEDA (neutra)",
         ([("FREELANCE", ORIGEN_ESTIMADO, 50000, 0)], [("CLASES", 1000)], [("SUPERMERCADO", 1000, 1000)], [],
          [("CAMBIO MONEDA", 70000, False)]),
         ([(i["concepto"], i["origen"], i["estimado_minor"], i["real_minor"]) for i in r_usd["ingresos"]["fijos"]],
          [(i["concepto"], i["real_minor"]) for i in r_usd["ingresos"]["variables"]],
          [(v["categoria"], v["estimado_minor"], v["real_minor"]) for v in r_usd["egresos"]["variables_sin_presupuesto"]],
          r_usd["tarjeta"]["por_cuenta"],
          [(c["categoria"], c["neto_minor"], c["cuenta"]) for c in r_usd["movimientos_capital"]["items"]]))
    r_sin = dashboard.get_resumen_mes(MES, ANIO)
    caso("sin usuario local: lo avisa y las DEUDAS son solo informales",
         (True, ["informal", "informal"], ["informal", "informal", "informal", "informal"]),
         (r_sin["sin_usuario_local"], [x["tipo"] for x in r_sin["deudas"]["me_deben"]],
          [x["tipo"] for x in r_sin["deudas"]["debo"]]))
    egreso("2026-11-10", "PAGO TARJETA", 5000000)  # noviembre: no vence nada y se paga igual
    r_nov = dashboard.get_resumen_mes(11, ANIO, usuario_local="bruno")
    caso("noviembre: nada a pagar, pagos 50.000, falta pagar 0 (nunca negativo)", ([], 0, 5000000, 0),
         (r_nov["tarjeta"]["por_cuenta"], r_nov["tarjeta"]["total_a_pagar_minor"],
          r_nov["tarjeta"]["pagos_realizados_minor"], r_nov["tarjeta"]["falta_pagar_minor"]))
    caso_excepcion("moneda desconocida → ValueError", ValueError,
                   lambda: dashboard.get_resumen_mes(MES, ANIO, moneda="XYZ"))
    caso_excepcion("mes 13 → ValueError", ValueError, lambda: dashboard.get_resumen_mes(13, ANIO))

    print(f"\n{casos_ok}/{casos_total} casos OK")
    print(f"(La DB temporal quedó en {db_path} — no es data/deltabalance.db.)")


if __name__ == "__main__":
    main()
