"""
verify/ahorros/verify_savings_service.py

Verifica SavingsService (services/savings_service.py) — creado desde cero
en Fase 2, bloque AHORROS paso 2, sin comportamiento previo que replicar.

Cubre:
  - create_activo()/list_activos(), create_objetivo()/list_objetivos().
  - register_purchase(): asignaciones que suman <100 (válido, queda una
    porción sin asignar) y que suman >100 (AsignacionInvalidaError, sin
    escribir nada).
  - register_return(): el escenario CENTRAL — dos compras del mismo activo
    con asignaciones distintas cada una (60/40 y 100%) y confirma que el
    rendimiento se reparte según la proporción AGREGADA de las dos compras
    juntas, no según una sola. Un segundo escenario con 3 objetivos y un
    monto que no divide parejo, para ejercitar de verdad el método del
    resto mayor. También el caso "total agregado = 0" (rendimiento sin
    poder repartirse).
  - register_sale(): Tarea 6d (docs/PROXIMOS_PASOS.md) — `asignaciones:
    list[dict]` en vez de un único `objetivo_id` obligatorio (mismo
    formato que register_purchase()). El caso viejo de "un solo objetivo
    al 100%" se re-expresa con la firma nueva y sigue pasando; caso nuevo
    de venta repartida entre dos objetivos; asignaciones vacías ahora
    ValueError (a diferencia de register_purchase(), acá no existe
    "venta libre"); objetivo inexistente y suma >100% con el mismo
    criterio que register_purchase().
  - get_balance_por_tipo()/get_balance_por_activo() (Tarea 6d): dos
    agregaciones nuevas de solo lectura, ninguna pasa por asignaciones/
    objetivos — el total real de movimientos_activo por tipo de activo o
    por activo_id, sin segregar por objetivo.
  - list_movimientos() (Tarea 6d, no pedido explícitamente en la
    consigna original — agregado porque el Registro de movimientos de
    ui/screens/ahorros.py lo necesita y no existía ningún método para
    listar movimientos de TODOS los activos, ni en el repositorio ni acá):
    filtros por rango de fecha/tipo de activo/tipo de movimiento/objetivo,
    y que cada movimiento devuelto trae sus asignaciones ya resueltas con
    nombre de objetivo (no solo el id).
  - get_objetivo_balance(): compra + rendimiento + venta parcial del mismo
    objetivo, confirmando el saldo neto.
  - Tarea 6b (docs/PROXIMOS_PASOS.md): register_purchase()/register_sale()
    con cuenta_id — crean la transacción real vinculada (egreso/ingreso),
    confirman el transaccion_id concreto persistido en el movimiento, y que
    el saldo real de la cuenta (CuentasRepository.obtener_saldo(), vista
    vw_balance_cuentas) baja/sube en consecuencia. Validaciones (cuenta_id
    sin categoria_id, cuenta_id/categoria_id inexistentes). Atomicidad REAL
    probada con rollback simulado vía monkeypatch de
    AsignacionesRepository.crear() — mismo patrón que
    verify_empleos_service.py::create_receipt() con cuenta_id — confirmando
    que ni la transacción ni el movimiento quedan huérfanos si la
    asignación falla a mitad de camino. get_balance_por_cuenta() con
    aportes desde dos cuentas distintas al mismo objetivo, confirmando que
    se agrupan por separado.
  - Tarea 1b: get_or_create_reserved_cash_asset() — primera vez crea el
    activo genérico "Efectivo reservado en <cuenta>", llamadas siguientes
    con el mismo nombre de cuenta lo REUSAN (no duplican), y un nombre de
    cuenta distinto crea un activo separado.

Correlo con:
    python verify/ahorros/verify_savings_service.py
"""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from verify._dummy_db import crear_dummy_db
from db.database import DatabaseManager
from repositories.cuentas_repository import CuentasRepository
from repositories.asignaciones_repository import AsignacionesRepository
from services.savings_service import (
    SavingsService,
    SavingsError,
    ActivoNotFoundError,
    ObjetivoNotFoundError,
    AsignacionInvalidaError,
    AccountNotFoundError,
    CategoryNotFoundError,
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
    svc = SavingsService(manager)

    moneda_ars = manager.fetchone("SELECT id FROM monedas WHERE codigo = 'ARS';")["id"]

    # ============================================================
    # ACTIVOS / OBJETIVOS — gestión simple
    # ============================================================
    print("--- create_activo() / list_activos() ---")
    activo_1 = svc.create_activo(nombre="FCI Ahorro", tipo="fci", moneda_id=moneda_ars)
    caso("create_activo() devuelve success=True", True, activo_1.success)
    caso("create_activo() devuelve entity_id numérico", True, isinstance(activo_1.entity_id, int))
    activo_1_id = activo_1.entity_id

    caso_excepcion(
        "create_activo() con moneda_id inexistente lanza SavingsError",
        SavingsError,
        lambda: svc.create_activo(nombre="X", tipo="fci", moneda_id=999999),
    )

    activo_2 = svc.create_activo(nombre="Cripto Wallet", tipo="cripto", moneda_id=moneda_ars).entity_id

    fci_listados = svc.list_activos(tipo="fci")
    caso("list_activos(tipo='fci') incluye activo_1", True, activo_1_id in [a["id"] for a in fci_listados])
    caso("list_activos(tipo='fci') excluye activo_2 (cripto)", False, activo_2 in [a["id"] for a in fci_listados])

    print("\n--- create_objetivo() / list_objetivos() ---")
    objetivo_A = svc.create_objetivo(nombre="Terreno", monto_meta_minor=50000000).entity_id
    objetivo_B = svc.create_objetivo(nombre="Vacaciones").entity_id
    objetivo_X = svc.create_objetivo(nombre="Moto").entity_id
    objetivo_Y = svc.create_objetivo(nombre="Auto").entity_id
    objetivo_Z = svc.create_objetivo(nombre="Casa").entity_id

    todos_objetivos = svc.list_objetivos()
    caso(
        "list_objetivos() incluye los 5 objetivos creados",
        True,
        all(o in [x["id"] for x in todos_objetivos] for o in (objetivo_A, objetivo_B, objetivo_X, objetivo_Y, objetivo_Z)),
    )

    # ============================================================
    # REGISTER_PURCHASE() — validación de asignaciones
    # ============================================================
    print("\n--- register_purchase() — asignaciones que suman <100% (válido) ---")
    compra_parcial = svc.register_purchase(
        activo_id=activo_2, fecha="2026-01-01", monto_total_minor=1000,
        asignaciones=[{"objetivo_id": objetivo_X, "porcentaje": 70.0}],
    )
    caso("register_purchase() con 70% asignado: success=True", True, compra_parcial.success)
    caso("register_purchase() con 70% asignado: crea 1 asignación", 1, len(compra_parcial.data["asignaciones"]))
    caso(
        "register_purchase() con 70% asignado: monto_asignado_minor = round(1000*70/100) = 700",
        700,
        compra_parcial.data["asignaciones"][0]["monto_asignado_minor"],
    )

    print("\n--- register_purchase() — asignaciones que suman >100% (AsignacionInvalidaError) ---")
    movimientos_antes = manager.fetchone("SELECT COUNT(*) AS n FROM movimientos_activo;")["n"]
    asignaciones_antes = manager.fetchone("SELECT COUNT(*) AS n FROM asignaciones;")["n"]
    caso_excepcion(
        "register_purchase() con asignaciones sumando 110% lanza AsignacionInvalidaError",
        AsignacionInvalidaError,
        lambda: svc.register_purchase(
            activo_id=activo_2, fecha="2026-01-02", monto_total_minor=1000,
            asignaciones=[
                {"objetivo_id": objetivo_X, "porcentaje": 60.0},
                {"objetivo_id": objetivo_Y, "porcentaje": 50.0},
            ],
        ),
    )
    movimientos_despues = manager.fetchone("SELECT COUNT(*) AS n FROM movimientos_activo;")["n"]
    asignaciones_despues = manager.fetchone("SELECT COUNT(*) AS n FROM asignaciones;")["n"]
    caso("AsignacionInvalidaError se valida ANTES de escribir: no se creó ningún movimiento", movimientos_antes, movimientos_despues)
    caso("AsignacionInvalidaError se valida ANTES de escribir: no se creó ninguna asignación", asignaciones_antes, asignaciones_despues)

    caso_excepcion(
        "register_purchase() con objetivo_id inexistente en asignaciones lanza ObjetivoNotFoundError",
        ObjetivoNotFoundError,
        lambda: svc.register_purchase(
            activo_id=activo_2, fecha="2026-01-02", monto_total_minor=1000,
            asignaciones=[{"objetivo_id": 999999, "porcentaje": 50.0}],
        ),
    )
    caso_excepcion(
        "register_purchase() con activo_id inexistente lanza ActivoNotFoundError",
        ActivoNotFoundError,
        lambda: svc.register_purchase(activo_id=999999, fecha="2026-01-02", monto_total_minor=1000),
    )

    print("\n--- register_purchase() sin asignaciones — compra 'libre' ---")
    compra_libre = svc.register_purchase(activo_id=activo_2, fecha="2026-01-03", monto_total_minor=500)
    caso("register_purchase() sin asignaciones: success=True", True, compra_libre.success)
    caso("register_purchase() sin asignaciones: data['asignado'] = False", False, compra_libre.data["asignado"])
    caso("register_purchase() sin asignaciones: no crea ninguna asignación", 0, len(compra_libre.data["asignaciones"]))

    # ============================================================
    # REGISTER_RETURN() — escenario central: proporción AGREGADA
    # de dos compras con asignaciones distintas cada una
    # ============================================================
    print("\n--- register_return() — escenario central: dos compras, proporción agregada ---")
    # compra_1: monto_total=1000, 60% objetivo_A (600) + 40% objetivo_B (400)
    compra_1 = svc.register_purchase(
        activo_id=activo_1_id, fecha="2026-01-05", monto_total_minor=1000,
        asignaciones=[
            {"objetivo_id": objetivo_A, "porcentaje": 60.0},
            {"objetivo_id": objetivo_B, "porcentaje": 40.0},
        ],
    )
    # compra_2: monto_total=500, 100% objetivo_A (500)
    compra_2 = svc.register_purchase(
        activo_id=activo_1_id, fecha="2026-02-05", monto_total_minor=500,
        asignaciones=[{"objetivo_id": objetivo_A, "porcentaje": 100.0}],
    )
    caso("compra_1: asignación a objetivo_A = 600", 600, compra_1.data["asignaciones"][0]["monto_asignado_minor"])
    caso("compra_1: asignación a objetivo_B = 400", 400, compra_1.data["asignaciones"][1]["monto_asignado_minor"])
    caso("compra_2: asignación a objetivo_A = 500", 500, compra_2.data["asignaciones"][0]["monto_asignado_minor"])

    # Agregado: objetivo_A = 600+500 = 1100, objetivo_B = 400, total = 1500
    # rendimiento = 100 -> A: 100*1100//1500=73, B: 100*400//1500=26, resto=1 -> A=74
    rendimiento_1 = svc.register_return(activo_id=activo_1_id, fecha="2026-03-01", monto_total_minor=100)
    caso("register_return() escenario central: success=True", True, rendimiento_1.success)
    caso("register_return() escenario central: data['repartido'] = True", True, rendimiento_1.data["repartido"])
    caso("register_return() escenario central: 2 asignaciones creadas", 2, len(rendimiento_1.data["asignaciones"]))

    reparto_por_objetivo = {a["objetivo_id"]: a["monto_asignado_minor"] for a in rendimiento_1.data["asignaciones"]}
    caso(
        "register_return() escenario central: objetivo_A recibe 74 (73 + resto de 1, por tener el mayor monto agregado)",
        74,
        reparto_por_objetivo[objetivo_A],
    )
    caso(
        "register_return() escenario central: objetivo_B recibe 26 (piso de 100*400//1500)",
        26,
        reparto_por_objetivo[objetivo_B],
    )
    caso(
        "register_return() escenario central: la suma de las asignaciones da EXACTO el monto_total_minor (100)",
        100,
        sum(reparto_por_objetivo.values()),
    )
    caso(
        "register_return() escenario central: se repartió según la proporción AGREGADA (A=1100/1500≈73.3%) y no solo según compra_1 (A=60%, hubiera dado 60 en vez de 74)",
        True,
        reparto_por_objetivo[objetivo_A] != 60,
    )

    print("\n--- register_return() — 3 objetivos, monto que fuerza el método del resto mayor ---")
    # una sola compra de activo_2 repartida 40/35/25 entre objetivo_X/Y/Z (activo_2 ya tenía compra_parcial/compra_libre previas sin tocar X/Y/Z salvo compra_parcial->X en 70%; usamos un activo nuevo para aislar el escenario)
    activo_3 = svc.create_activo(nombre="Plazo Fijo", tipo="plazo_fijo", moneda_id=moneda_ars).entity_id
    svc.register_purchase(
        activo_id=activo_3, fecha="2026-01-10", monto_total_minor=1000000,
        asignaciones=[
            {"objetivo_id": objetivo_X, "porcentaje": 40.0},
            {"objetivo_id": objetivo_Y, "porcentaje": 35.0},
            {"objetivo_id": objetivo_Z, "porcentaje": 25.0},
        ],
    )
    # Agregado: X=400000, Y=350000, Z=250000, total=1000000
    # rendimiento=101 -> X:101*400000//1000000=40, Y:101*350000//1000000=35, Z:101*250000//1000000=25, resto=1 -> X=41 (mayor monto agregado)
    rendimiento_2 = svc.register_return(activo_id=activo_3, fecha="2026-02-10", monto_total_minor=101)
    reparto_3 = {a["objetivo_id"]: a["monto_asignado_minor"] for a in rendimiento_2.data["asignaciones"]}
    caso("register_return() con 3 objetivos: objetivo_X recibe 41 (40 + resto)", 41, reparto_3[objetivo_X])
    caso("register_return() con 3 objetivos: objetivo_Y recibe 35", 35, reparto_3[objetivo_Y])
    caso("register_return() con 3 objetivos: objetivo_Z recibe 25", 25, reparto_3[objetivo_Z])
    caso(
        "register_return() con 3 objetivos: la suma da EXACTO el monto_total_minor (101), no 100",
        101,
        sum(reparto_3.values()),
    )

    print("\n--- register_return() — total agregado = 0 (sin poder repartirse) ---")
    activo_4 = svc.create_activo(nombre="Sin asignar", tipo="otro", moneda_id=moneda_ars).entity_id
    svc.register_purchase(activo_id=activo_4, fecha="2026-01-15", monto_total_minor=999)  # compra libre, sin asignaciones
    rendimiento_sin_repartir = svc.register_return(activo_id=activo_4, fecha="2026-02-15", monto_total_minor=50)
    caso("register_return() sin asignaciones previas: success=True (movimiento se crea igual)", True, rendimiento_sin_repartir.success)
    caso("register_return() sin asignaciones previas: data['repartido'] = False", False, rendimiento_sin_repartir.data["repartido"])
    caso("register_return() sin asignaciones previas: no crea ninguna asignación", 0, len(rendimiento_sin_repartir.data["asignaciones"]))

    caso_excepcion(
        "register_return() con activo_id inexistente lanza ActivoNotFoundError",
        ActivoNotFoundError,
        lambda: svc.register_return(activo_id=999999, fecha="2026-01-01", monto_total_minor=100),
    )

    # ============================================================
    # REGISTER_SALE() — asignación explícita
    # ============================================================
    print("\n--- register_sale() — asignaciones: un solo objetivo al 100% (caso viejo, nueva firma) ---")
    venta_1 = svc.register_sale(
        activo_id=activo_1_id, fecha="2026-04-01", monto_total_minor=300,
        asignaciones=[{"objetivo_id": objetivo_A, "porcentaje": 100.0}],
    )
    caso("register_sale(): success=True", True, venta_1.success)
    caso("register_sale(): crea 1 asignación", 1, len(venta_1.data["asignaciones"]))
    caso("register_sale(): asignación 100% al objetivo elegido", objetivo_A, venta_1.data["asignaciones"][0]["objetivo_id"])
    caso(
        "register_sale(): monto_asignado_minor = monto_total_minor completo",
        300, venta_1.data["asignaciones"][0]["monto_asignado_minor"],
    )

    venta_2 = svc.register_sale(
        activo_id=activo_1_id, fecha="2026-04-02", monto_total_minor=200,
        asignaciones=[{"objetivo_id": objetivo_B, "porcentaje": 100.0}],
    )
    caso(
        "register_sale() a un segundo objetivo: monto_asignado_minor correcto",
        200, venta_2.data["asignaciones"][0]["monto_asignado_minor"],
    )

    print("\n--- register_sale() — asignaciones: venta repartida entre DOS objetivos (caso nuevo, Tarea 6d) ---")
    venta_repartida = svc.register_sale(
        activo_id=activo_1_id, fecha="2026-04-04", monto_total_minor=1000,
        asignaciones=[
            {"objetivo_id": objetivo_X, "porcentaje": 65.0},
            {"objetivo_id": objetivo_Y, "porcentaje": 35.0},
        ],
    )
    caso("register_sale() repartida: success=True", True, venta_repartida.success)
    caso("register_sale() repartida: crea 2 asignaciones", 2, len(venta_repartida.data["asignaciones"]))
    reparto_venta = {a["objetivo_id"]: a["monto_asignado_minor"] for a in venta_repartida.data["asignaciones"]}
    caso("register_sale() repartida: objetivo_X = round(1000*65/100) = 650", 650, reparto_venta[objetivo_X])
    caso("register_sale() repartida: objetivo_Y = round(1000*35/100) = 350", 350, reparto_venta[objetivo_Y])
    caso("register_sale() repartida: la suma da EXACTO el monto_total_minor (1000)", 1000, sum(reparto_venta.values()))

    caso_excepcion(
        "register_sale() con asignaciones vacías lanza ValueError (una venta siempre sale de al menos un objetivo)",
        ValueError,
        lambda: svc.register_sale(activo_id=activo_1_id, fecha="2026-04-03", monto_total_minor=100, asignaciones=[]),
    )
    caso_excepcion(
        "register_sale() con objetivo_id inexistente en asignaciones lanza ObjetivoNotFoundError",
        ObjetivoNotFoundError,
        lambda: svc.register_sale(
            activo_id=activo_1_id, fecha="2026-04-03", monto_total_minor=100,
            asignaciones=[{"objetivo_id": 999999, "porcentaje": 100.0}],
        ),
    )
    caso_excepcion(
        "register_sale() con asignaciones sumando >100% lanza AsignacionInvalidaError (mismo criterio que register_purchase())",
        AsignacionInvalidaError,
        lambda: svc.register_sale(
            activo_id=activo_1_id, fecha="2026-04-03", monto_total_minor=100,
            asignaciones=[
                {"objetivo_id": objetivo_X, "porcentaje": 60.0},
                {"objetivo_id": objetivo_Y, "porcentaje": 50.0},
            ],
        ),
    )

    # ============================================================
    # GET_OBJETIVO_BALANCE() — compra + rendimiento + venta parcial
    # ============================================================
    print("\n--- get_objetivo_balance() — compra + rendimiento + venta parcial del mismo objetivo ---")
    # objetivo_A: invertido = 600 (compra_1) + 500 (compra_2) = 1100
    #             rendimiento = 74 (rendimiento_1)
    #             retirado = 300 (venta_1, parcial: no vendió todo lo invertido)
    #             saldo_neto = 1100 + 74 - 300 = 874
    balance_A = svc.get_objetivo_balance(objetivo_A)
    caso("get_objetivo_balance(objetivo_A): invertido_minor = 1100", 1100, balance_A["invertido_minor"])
    caso("get_objetivo_balance(objetivo_A): rendimiento_minor = 74", 74, balance_A["rendimiento_minor"])
    caso("get_objetivo_balance(objetivo_A): retirado_minor = 300 (venta parcial)", 300, balance_A["retirado_minor"])
    caso("get_objetivo_balance(objetivo_A): saldo_neto_minor = 1100 + 74 - 300 = 874", 874, balance_A["saldo_neto_minor"])

    # objetivo_B: invertido = 400 (compra_1), rendimiento = 26 (rendimiento_1), retirado = 200 (venta_2)
    balance_B = svc.get_objetivo_balance(objetivo_B)
    caso("get_objetivo_balance(objetivo_B): saldo_neto_minor = 400 + 26 - 200 = 226", 226, balance_B["saldo_neto_minor"])

    caso_excepcion(
        "get_objetivo_balance() con objetivo_id inexistente lanza ObjetivoNotFoundError",
        ObjetivoNotFoundError,
        lambda: svc.get_objetivo_balance(999999),
    )

    # ============================================================
    # REGISTER_PURCHASE()/REGISTER_SALE() CON cuenta_id — Tarea 6b
    # ============================================================
    cuentas_repo = CuentasRepository(manager)
    categoria_inversiones = manager.fetchone(
        "SELECT id FROM categorias WHERE categoria_principal = 'MOVIMIENTO CAPITAL' "
        "AND subcategoria = 'Inversiones';"
    )["id"]

    cuenta_mp = cuentas_repo.crear(
        nombre="Mercado Pago (verify)", tipo="debito", moneda_codigo="ARS", saldo_inicial=1000.0,
    )
    activo_link = svc.create_activo(nombre="FCI vinculado", tipo="fci", moneda_id=moneda_ars).entity_id
    objetivo_link = svc.create_objetivo(nombre="Vinculo cuenta").entity_id

    print("\n--- register_purchase() SIN cuenta_id — no crea transacción, transaccion_id queda NULL ---")
    transacciones_antes_libre = manager.fetchone("SELECT COUNT(*) AS n FROM transacciones;")["n"]
    compra_sin_cuenta = svc.register_purchase(
        activo_id=activo_link, fecha="2026-05-01", monto_total_minor=10000,
        asignaciones=[{"objetivo_id": objetivo_link, "porcentaje": 100.0}],
    )
    caso("register_purchase() sin cuenta_id: data['transaccion_id'] = None", None, compra_sin_cuenta.data["transaccion_id"])
    transacciones_despues_libre = manager.fetchone("SELECT COUNT(*) AS n FROM transacciones;")["n"]
    caso("register_purchase() sin cuenta_id: no crea ninguna transacción", transacciones_antes_libre, transacciones_despues_libre)
    fila_movimiento_libre = manager.fetchone(
        "SELECT transaccion_id FROM movimientos_activo WHERE id = ?;", (compra_sin_cuenta.entity_id,)
    )
    caso("register_purchase() sin cuenta_id: movimiento persiste transaccion_id NULL", None, fila_movimiento_libre["transaccion_id"])

    print("\n--- register_purchase() con cuenta_id — validaciones ---")
    caso_excepcion(
        "register_purchase() con cuenta_id pero SIN categoria_id lanza ValueError",
        ValueError,
        lambda: svc.register_purchase(
            activo_id=activo_link, fecha="2026-05-02", monto_total_minor=1000, cuenta_id=cuenta_mp,
        ),
    )
    caso_excepcion(
        "register_purchase() con cuenta_id inexistente lanza AccountNotFoundError",
        AccountNotFoundError,
        lambda: svc.register_purchase(
            activo_id=activo_link, fecha="2026-05-02", monto_total_minor=1000,
            cuenta_id=999999, categoria_id=categoria_inversiones,
        ),
    )
    caso_excepcion(
        "register_purchase() con categoria_id inexistente lanza CategoryNotFoundError",
        CategoryNotFoundError,
        lambda: svc.register_purchase(
            activo_id=activo_link, fecha="2026-05-02", monto_total_minor=1000,
            cuenta_id=cuenta_mp, categoria_id=999999,
        ),
    )

    print("\n--- register_purchase() con cuenta_id — camino exitoso: transacción real + saldo baja ---")
    saldo_mp_antes = cuentas_repo.obtener_saldo(cuenta_mp, "ARS")
    caso("saldo inicial de Mercado Pago (verify) = 1000.0", 1000.0, saldo_mp_antes)

    compra_con_cuenta = svc.register_purchase(
        activo_id=activo_link, fecha="2026-05-03", monto_total_minor=50000,  # 500.00 ARS
        asignaciones=[{"objetivo_id": objetivo_link, "porcentaje": 100.0}],
        cuenta_id=cuenta_mp, categoria_id=categoria_inversiones,
    )
    caso("register_purchase() con cuenta_id: success=True", True, compra_con_cuenta.success)
    caso("register_purchase() con cuenta_id: data['transaccion_id'] es numérico", True, isinstance(compra_con_cuenta.data["transaccion_id"], int))

    fila_transaccion_compra = manager.fetchone(
        "SELECT * FROM transacciones WHERE id = ?;", (compra_con_cuenta.data["transaccion_id"],)
    )
    caso("la transacción creada por register_purchase() es tipo_movimiento='egreso'", "egreso", fila_transaccion_compra["tipo_movimiento"])
    caso("la transacción creada tiene el monto_minor del aporte", 50000, fila_transaccion_compra["monto_minor"])
    caso("la transacción creada usa cuenta_id=cuenta_mp", cuenta_mp, fila_transaccion_compra["cuenta_id"])
    caso("la transacción creada usa la categoria_id pasada", categoria_inversiones, fila_transaccion_compra["categoria_id"])

    fila_movimiento_compra = manager.fetchone(
        "SELECT transaccion_id FROM movimientos_activo WHERE id = ?;", (compra_con_cuenta.entity_id,)
    )
    caso(
        "el movimiento_activo queda vinculado al transaccion_id concreto de la transacción creada",
        compra_con_cuenta.data["transaccion_id"],
        fila_movimiento_compra["transaccion_id"],
    )

    saldo_mp_tras_compra = cuentas_repo.obtener_saldo(cuenta_mp, "ARS")
    caso("el saldo de Mercado Pago (verify) baja 500.00 tras el aporte (egreso)", 500.0, saldo_mp_tras_compra)

    print("\n--- register_sale() con cuenta_id — camino exitoso: transacción real (ingreso) + saldo sube ---")
    venta_con_cuenta = svc.register_sale(
        activo_id=activo_link, fecha="2026-05-10", monto_total_minor=20000,  # 200.00 ARS
        asignaciones=[{"objetivo_id": objetivo_link, "porcentaje": 100.0}],
        cuenta_id=cuenta_mp, categoria_id=categoria_inversiones,
    )
    caso("register_sale() con cuenta_id: success=True", True, venta_con_cuenta.success)
    caso("register_sale() con cuenta_id: data['transaccion_id'] es numérico", True, isinstance(venta_con_cuenta.data["transaccion_id"], int))

    fila_transaccion_venta = manager.fetchone(
        "SELECT * FROM transacciones WHERE id = ?;", (venta_con_cuenta.data["transaccion_id"],)
    )
    caso("la transacción creada por register_sale() es tipo_movimiento='ingreso'", "ingreso", fila_transaccion_venta["tipo_movimiento"])
    caso("la transacción creada tiene el monto_minor del retiro", 20000, fila_transaccion_venta["monto_minor"])

    fila_movimiento_venta = manager.fetchone(
        "SELECT transaccion_id FROM movimientos_activo WHERE id = ?;", (venta_con_cuenta.entity_id,)
    )
    caso(
        "el movimiento_activo de la venta queda vinculado al transaccion_id concreto",
        venta_con_cuenta.data["transaccion_id"],
        fila_movimiento_venta["transaccion_id"],
    )

    saldo_mp_tras_venta = cuentas_repo.obtener_saldo(cuenta_mp, "ARS")
    caso("el saldo de Mercado Pago (verify) sube 200.00 tras el retiro (ingreso): 500.00 + 200.00 = 700.00", 700.0, saldo_mp_tras_venta)

    caso_excepcion(
        "register_sale() con cuenta_id pero SIN categoria_id lanza ValueError",
        ValueError,
        lambda: svc.register_sale(
            activo_id=activo_link, fecha="2026-05-11", monto_total_minor=1000,
            asignaciones=[{"objetivo_id": objetivo_link, "porcentaje": 100.0}], cuenta_id=cuenta_mp,
        ),
    )

    print("\n--- register_purchase() con cuenta_id — atomicidad REAL con rollback simulado ---")
    transacciones_antes_rollback = manager.fetchone("SELECT COUNT(*) AS n FROM transacciones;")["n"]
    movimientos_antes_rollback = manager.fetchone("SELECT COUNT(*) AS n FROM movimientos_activo;")["n"]
    asignaciones_antes_rollback = manager.fetchone("SELECT COUNT(*) AS n FROM asignaciones;")["n"]
    saldo_mp_antes_rollback = cuentas_repo.obtener_saldo(cuenta_mp, "ARS")

    metodo_original_asignaciones = AsignacionesRepository.crear

    def crear_asignacion_que_falla(self, *args, **kwargs):
        raise RuntimeError("Fallo simulado en AsignacionesRepository.crear(), a mitad de la transacción externa")

    AsignacionesRepository.crear = crear_asignacion_que_falla
    try:
        caso_excepcion(
            "register_purchase() con cuenta_id propaga el fallo simulado de AsignacionesRepository.crear()",
            RuntimeError,
            lambda: svc.register_purchase(
                activo_id=activo_link, fecha="2026-05-12", monto_total_minor=30000,
                asignaciones=[{"objetivo_id": objetivo_link, "porcentaje": 100.0}],
                cuenta_id=cuenta_mp, categoria_id=categoria_inversiones,
            ),
        )
    finally:
        AsignacionesRepository.crear = metodo_original_asignaciones

    transacciones_despues_rollback = manager.fetchone("SELECT COUNT(*) AS n FROM transacciones;")["n"]
    movimientos_despues_rollback = manager.fetchone("SELECT COUNT(*) AS n FROM movimientos_activo;")["n"]
    asignaciones_despues_rollback = manager.fetchone("SELECT COUNT(*) AS n FROM asignaciones;")["n"]
    saldo_mp_despues_rollback = cuentas_repo.obtener_saldo(cuenta_mp, "ARS")

    caso("rollback revierte el INSERT de la transacción (no queda huérfana)", transacciones_antes_rollback, transacciones_despues_rollback)
    caso("rollback revierte el INSERT del movimiento (no queda huérfano)", movimientos_antes_rollback, movimientos_despues_rollback)
    caso("rollback revierte también la asignación (nunca llegó a crearse)", asignaciones_antes_rollback, asignaciones_despues_rollback)
    caso("rollback: el saldo de Mercado Pago (verify) no cambió", saldo_mp_antes_rollback, saldo_mp_despues_rollback)

    print("\n--- register_sale() con cuenta_id — atomicidad REAL con rollback simulado ---")
    transacciones_antes_rollback_venta = manager.fetchone("SELECT COUNT(*) AS n FROM transacciones;")["n"]
    movimientos_antes_rollback_venta = manager.fetchone("SELECT COUNT(*) AS n FROM movimientos_activo;")["n"]
    saldo_mp_antes_rollback_venta = cuentas_repo.obtener_saldo(cuenta_mp, "ARS")

    AsignacionesRepository.crear = crear_asignacion_que_falla
    try:
        caso_excepcion(
            "register_sale() con cuenta_id propaga el fallo simulado de AsignacionesRepository.crear()",
            RuntimeError,
            lambda: svc.register_sale(
                activo_id=activo_link, fecha="2026-05-13", monto_total_minor=5000,
                asignaciones=[{"objetivo_id": objetivo_link, "porcentaje": 100.0}],
                cuenta_id=cuenta_mp, categoria_id=categoria_inversiones,
            ),
        )
    finally:
        AsignacionesRepository.crear = metodo_original_asignaciones

    transacciones_despues_rollback_venta = manager.fetchone("SELECT COUNT(*) AS n FROM transacciones;")["n"]
    movimientos_despues_rollback_venta = manager.fetchone("SELECT COUNT(*) AS n FROM movimientos_activo;")["n"]
    saldo_mp_despues_rollback_venta = cuentas_repo.obtener_saldo(cuenta_mp, "ARS")

    caso(
        "rollback (venta) revierte el INSERT de la transacción de ingreso",
        transacciones_antes_rollback_venta, transacciones_despues_rollback_venta,
    )
    caso(
        "rollback (venta) revierte el INSERT del movimiento (no queda huérfano)",
        movimientos_antes_rollback_venta, movimientos_despues_rollback_venta,
    )
    caso("rollback (venta): el saldo de Mercado Pago (verify) no cambió", saldo_mp_antes_rollback_venta, saldo_mp_despues_rollback_venta)

    # ============================================================
    # GET_BALANCE_POR_CUENTA() — Tarea 6b
    # ============================================================
    print("\n--- get_balance_por_cuenta() — aportes desde dos cuentas distintas al mismo objetivo ---")
    cuenta_a = cuentas_repo.crear(nombre="Cuenta A (verify balance)", tipo="debito", moneda_codigo="ARS", saldo_inicial=0.0)
    cuenta_b = cuentas_repo.crear(nombre="Cuenta B (verify balance)", tipo="inversion", moneda_codigo="ARS", saldo_inicial=0.0)
    objetivo_balance_cuentas = svc.create_objetivo(nombre="Balance multi-cuenta").entity_id

    svc.register_purchase(
        activo_id=activo_link, fecha="2026-06-01", monto_total_minor=40000,
        asignaciones=[{"objetivo_id": objetivo_balance_cuentas, "porcentaje": 100.0}],
        cuenta_id=cuenta_a, categoria_id=categoria_inversiones,
    )
    svc.register_purchase(
        activo_id=activo_link, fecha="2026-06-02", monto_total_minor=15000,
        asignaciones=[{"objetivo_id": objetivo_balance_cuentas, "porcentaje": 100.0}],
        cuenta_id=cuenta_b, categoria_id=categoria_inversiones,
    )
    # Un retiro parcial desde cuenta_a: debe descontarse del saldo de esa cuenta puntual, no de cuenta_b.
    svc.register_sale(
        activo_id=activo_link, fecha="2026-06-05", monto_total_minor=10000,
        asignaciones=[{"objetivo_id": objetivo_balance_cuentas, "porcentaje": 100.0}],
        cuenta_id=cuenta_a, categoria_id=categoria_inversiones,
    )
    # Un aporte informal (sin cuenta_id) no debe aparecer en el desglose por cuenta.
    svc.register_purchase(
        activo_id=activo_link, fecha="2026-06-06", monto_total_minor=999999,
        asignaciones=[{"objetivo_id": objetivo_balance_cuentas, "porcentaje": 100.0}],
    )

    balance_por_cuenta = svc.get_balance_por_cuenta(objetivo_balance_cuentas)
    balance_por_cuenta_id = {fila["cuenta_id"]: fila for fila in balance_por_cuenta}

    caso("get_balance_por_cuenta(): devuelve exactamente 2 cuentas (la informal no cuenta)", 2, len(balance_por_cuenta))
    caso("get_balance_por_cuenta(): incluye cuenta_a", True, cuenta_a in balance_por_cuenta_id)
    caso("get_balance_por_cuenta(): incluye cuenta_b", True, cuenta_b in balance_por_cuenta_id)
    caso("get_balance_por_cuenta(): cuenta_a aportado_minor = 40000", 40000, balance_por_cuenta_id[cuenta_a]["aportado_minor"])
    caso("get_balance_por_cuenta(): cuenta_a retirado_minor = 10000", 10000, balance_por_cuenta_id[cuenta_a]["retirado_minor"])
    caso("get_balance_por_cuenta(): cuenta_a saldo_minor = 40000 - 10000 = 30000", 30000, balance_por_cuenta_id[cuenta_a]["saldo_minor"])
    caso("get_balance_por_cuenta(): cuenta_b aportado_minor = 15000 (sin retiros)", 15000, balance_por_cuenta_id[cuenta_b]["aportado_minor"])
    caso("get_balance_por_cuenta(): cuenta_b retirado_minor = 0", 0, balance_por_cuenta_id[cuenta_b]["retirado_minor"])
    caso("get_balance_por_cuenta(): cuenta_b saldo_minor = 15000", 15000, balance_por_cuenta_id[cuenta_b]["saldo_minor"])
    caso("get_balance_por_cuenta(): las dos cuentas quedan agrupadas por separado (ids distintos)", True, cuenta_a != cuenta_b)

    caso_excepcion(
        "get_balance_por_cuenta() con objetivo_id inexistente lanza ObjetivoNotFoundError",
        ObjetivoNotFoundError,
        lambda: svc.get_balance_por_cuenta(999999),
    )
    caso("get_balance_por_cuenta() de un objetivo sin ningún movimiento vinculado a cuenta devuelve []", [], svc.get_balance_por_cuenta(objetivo_B))

    # ============================================================
    # GET_BALANCE_POR_TIPO() / GET_BALANCE_POR_ACTIVO() — Tarea 6d
    # ============================================================
    print("\n--- get_balance_por_tipo() / get_balance_por_activo() — activos nuevos y aislados ---")
    # Activos nuevos a propósito (no reusar activo_1_id/activo_2/activo_link,
    # que ya acumularon actividad de secciones anteriores del script) — así
    # los números esperados se pueden calcular a mano sin sumar todo lo de
    # arriba.
    activo_accion_1 = svc.create_activo(nombre="Accion Uno (verify tipo)", tipo="accion", moneda_id=moneda_ars).entity_id
    activo_accion_2 = svc.create_activo(nombre="Accion Dos (verify tipo)", tipo="accion", moneda_id=moneda_ars).entity_id
    activo_cripto_1 = svc.create_activo(nombre="Cripto Uno (verify tipo)", tipo="cripto", moneda_id=moneda_ars).entity_id

    # activo_accion_1: compra 10 unidades (100000) + rendimiento (5000) + venta 3 unidades (30000)
    svc.register_purchase(activo_id=activo_accion_1, fecha="2026-07-01", monto_total_minor=100000, cantidad=10.0)
    svc.register_return(activo_id=activo_accion_1, fecha="2026-07-02", monto_total_minor=5000)
    svc.register_sale(
        activo_id=activo_accion_1, fecha="2026-07-03", monto_total_minor=30000, cantidad=3.0,
        asignaciones=[{"objetivo_id": objetivo_A, "porcentaje": 100.0}],
    )
    # saldo_neto = 100000 + 5000 - 30000 = 75000 ; cantidad_neta = 10.0 - 3.0 = 7.0

    # activo_accion_2: solo una compra de 5 unidades (50000), sin venta ni rendimiento
    svc.register_purchase(activo_id=activo_accion_2, fecha="2026-07-01", monto_total_minor=50000, cantidad=5.0)
    # saldo_neto = 50000 ; cantidad_neta = 5.0

    # activo_cripto_1: tipo distinto, mismo moneda_id — no debe mezclarse con 'accion'
    svc.register_purchase(activo_id=activo_cripto_1, fecha="2026-07-01", monto_total_minor=20000, cantidad=1000.0)
    # saldo_neto = 20000 ; cantidad_neta = 1000.0

    balance_tipo = svc.get_balance_por_tipo()
    balance_tipo_por_clave = {(f["tipo"], f["moneda_id"]): f for f in balance_tipo}

    caso(
        "get_balance_por_tipo(): 'accion'/ARS suma activo_accion_1 (75000) + activo_accion_2 (50000) = 125000",
        125000,
        balance_tipo_por_clave[("accion", moneda_ars)]["saldo_neto_minor"],
    )
    caso(
        "get_balance_por_tipo(): 'cripto'/ARS = 20000, sin mezclarse con 'accion'",
        20000,
        balance_tipo_por_clave[("cripto", moneda_ars)]["saldo_neto_minor"],
    )

    balance_activo = svc.get_balance_por_activo()
    balance_activo_por_id = {f["activo_id"]: f for f in balance_activo}

    caso(
        "get_balance_por_activo(): activo_accion_1 saldo_neto_minor = 100000 + 5000 - 30000 = 75000",
        75000, balance_activo_por_id[activo_accion_1]["saldo_neto_minor"],
    )
    caso(
        "get_balance_por_activo(): activo_accion_1 cantidad_neta = 10.0 - 3.0 = 7.0",
        7.0, balance_activo_por_id[activo_accion_1]["cantidad_neta"],
    )
    caso(
        "get_balance_por_activo(): activo_accion_2 saldo_neto_minor = 50000 (sin ventas)",
        50000, balance_activo_por_id[activo_accion_2]["saldo_neto_minor"],
    )
    caso(
        "get_balance_por_activo(): activo_accion_2 cantidad_neta = 5.0",
        5.0, balance_activo_por_id[activo_accion_2]["cantidad_neta"],
    )
    caso(
        "get_balance_por_activo(): activo_cripto_1 saldo_neto_minor = 20000",
        20000, balance_activo_por_id[activo_cripto_1]["saldo_neto_minor"],
    )
    # activo_4 (tipo='otro', creado en la sección de register_return()) tuvo
    # una compra libre (999) y un rendimiento (50) — NUNCA cargó `cantidad`
    # en ningún movimiento (register_return() ni siquiera acepta ese
    # parámetro) — cantidad_neta debe ser None, no 0.
    caso(
        "get_balance_por_activo(): activo_4 nunca cargó cantidad -> cantidad_neta = None (no 0 engañoso)",
        None, balance_activo_por_id[activo_4]["cantidad_neta"],
    )
    caso(
        "get_balance_por_activo(): activo_4 saldo_neto_minor = 999 (compra) + 50 (rendimiento) = 1049",
        1049, balance_activo_por_id[activo_4]["saldo_neto_minor"],
    )

    # ============================================================
    # LIST_MOVIMIENTOS() — Tarea 6d
    # ============================================================
    # No pedido explícitamente en la consigna (que solo enumeraba
    # get_balance_por_tipo()/get_balance_por_activo() como métodos
    # nuevos) — se agregó porque el "Registro de movimientos" de
    # ui/screens/ahorros.py (Parte D) necesita listar movimientos de
    # CUALQUIER activo, y ni el repositorio (solo listar_por_activo()/
    # listar_por_tipo(), ambos exigen un activo_id puntual) ni el
    # service tenían antes un método así. Reusa los activos/movimientos
    # ya creados arriba (activo_accion_1/2, activo_cripto_1, todos
    # fechados 2026-07-0X) — acotar por fecha a ese rango aísla estos
    # casos del resto de la actividad del script sin tener que
    # recontar todo desde cero.
    print("\n--- list_movimientos() — Tarea 6d ---")
    movimientos_julio = svc.list_movimientos(fecha_desde="2026-07-01", fecha_hasta="2026-07-03")
    caso("list_movimientos() con rango de fecha: devuelve los 5 movimientos de la sección anterior", 5, len(movimientos_julio))
    caso(
        "list_movimientos() ordena por fecha DESC (el más reciente primero)",
        "2026-07-03", movimientos_julio[0]["fecha"],
    )

    movimientos_solo_venta = svc.list_movimientos(fecha_desde="2026-07-01", fecha_hasta="2026-07-03", tipo_movimiento="venta")
    caso("list_movimientos(tipo_movimiento='venta'): devuelve exactamente 1 (la venta de activo_accion_1)", 1, len(movimientos_solo_venta))
    caso("list_movimientos(tipo_movimiento='venta'): activo_id correcto", activo_accion_1, movimientos_solo_venta[0]["activo_id"])
    caso("list_movimientos(tipo_movimiento='venta'): cantidad = 3.0", 3.0, movimientos_solo_venta[0]["cantidad"])
    caso("list_movimientos(tipo_movimiento='venta'): trae 1 asignación resuelta", 1, len(movimientos_solo_venta[0]["asignaciones"]))
    caso(
        "list_movimientos(tipo_movimiento='venta'): objetivo_nombre resuelto = 'Terreno' (no solo el id)",
        "Terreno", movimientos_solo_venta[0]["asignaciones"][0]["objetivo_nombre"],
    )

    movimientos_solo_cripto = svc.list_movimientos(fecha_desde="2026-07-01", fecha_hasta="2026-07-03", tipo_activo="cripto")
    caso("list_movimientos(tipo_activo='cripto'): devuelve exactamente 1 (activo_cripto_1)", 1, len(movimientos_solo_cripto))
    caso(
        "list_movimientos(tipo_activo='cripto'): sin asignaciones -> lista vacía, no None",
        [], movimientos_solo_cripto[0]["asignaciones"],
    )

    movimientos_objetivo_a = svc.list_movimientos(fecha_desde="2026-07-01", fecha_hasta="2026-07-03", objetivo_id=objetivo_A)
    caso("list_movimientos(objetivo_id=objetivo_A): devuelve exactamente 1 (la venta asignada a Terreno)", 1, len(movimientos_objetivo_a))

    caso("list_movimientos() sin actividad en el rango devuelve []", [], svc.list_movimientos(fecha_desde="2020-01-01", fecha_hasta="2020-01-02"))

    # ============================================================
    # GET_OR_CREATE_RESERVED_CASH_ASSET() — Tarea 1b
    # ============================================================
    print("\n--- get_or_create_reserved_cash_asset() — primera vez crea, segunda vez reusa ---")
    activos_antes = manager.fetchone("SELECT COUNT(*) AS n FROM activos_financieros;")["n"]
    primera_vez = svc.get_or_create_reserved_cash_asset(cuenta_nombre="Mercado Pago (verify)", moneda_id=moneda_ars)
    caso("get_or_create_reserved_cash_asset() primera vez: success=True", True, primera_vez.success)
    caso("get_or_create_reserved_cash_asset() primera vez: data['creado'] = True", True, primera_vez.data["creado"])
    caso(
        "get_or_create_reserved_cash_asset() arma el nombre exacto 'Efectivo reservado en <cuenta>'",
        "Efectivo reservado en Mercado Pago (verify)",
        primera_vez.data["nombre"],
    )
    activos_tras_primera = manager.fetchone("SELECT COUNT(*) AS n FROM activos_financieros;")["n"]
    caso("get_or_create_reserved_cash_asset() primera vez: crea exactamente 1 activo nuevo", activos_antes + 1, activos_tras_primera)

    fila_activo = manager.fetchone("SELECT * FROM activos_financieros WHERE id = ?;", (primera_vez.entity_id,))
    caso("el activo creado tiene tipo='otro'", "otro", fila_activo["tipo"])
    caso("el activo creado usa el moneda_id pasado", moneda_ars, fila_activo["moneda_id"])

    segunda_vez = svc.get_or_create_reserved_cash_asset(cuenta_nombre="Mercado Pago (verify)", moneda_id=moneda_ars)
    caso("get_or_create_reserved_cash_asset() segunda vez: success=True", True, segunda_vez.success)
    caso("get_or_create_reserved_cash_asset() segunda vez: data['creado'] = False (reusa)", False, segunda_vez.data["creado"])
    caso(
        "get_or_create_reserved_cash_asset() segunda vez: devuelve el MISMO entity_id (no crea uno nuevo)",
        primera_vez.entity_id,
        segunda_vez.entity_id,
    )
    activos_tras_segunda = manager.fetchone("SELECT COUNT(*) AS n FROM activos_financieros;")["n"]
    caso(
        "get_or_create_reserved_cash_asset() segunda vez: NO crea un activo nuevo (sigue en el mismo conteo)",
        activos_tras_primera,
        activos_tras_segunda,
    )

    print("\n--- get_or_create_reserved_cash_asset() — cuenta distinta crea un activo separado ---")
    otra_cuenta = svc.get_or_create_reserved_cash_asset(cuenta_nombre="Cocos FCI (verify)", moneda_id=moneda_ars)
    caso("get_or_create_reserved_cash_asset() con otro nombre de cuenta: crea uno nuevo", True, otra_cuenta.data["creado"])
    caso(
        "get_or_create_reserved_cash_asset() con otro nombre de cuenta: entity_id distinto al de Mercado Pago",
        True,
        otra_cuenta.entity_id != primera_vez.entity_id,
    )

    caso_excepcion(
        "get_or_create_reserved_cash_asset() con moneda_id inexistente (y activo todavía no creado) lanza SavingsError",
        SavingsError,
        lambda: svc.get_or_create_reserved_cash_asset(cuenta_nombre="Cuenta nueva sin moneda válida (verify)", moneda_id=999999),
    )

    manager.desconectar()

    print(f"\n--- Resumen: {casos_ok}/{casos_total} casos OK ---")


if __name__ == "__main__":
    main()
