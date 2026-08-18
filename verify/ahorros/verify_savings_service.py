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
  - register_sale(): asignación explícita a un solo objetivo.
  - get_objetivo_balance(): compra + rendimiento + venta parcial del mismo
    objetivo, confirmando el saldo neto.

Correlo con:
    python verify/ahorros/verify_savings_service.py
"""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from verify._dummy_db import crear_dummy_db
from db.database import DatabaseManager
from services.savings_service import (
    SavingsService,
    SavingsError,
    ActivoNotFoundError,
    ObjetivoNotFoundError,
    AsignacionInvalidaError,
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
    print("\n--- register_sale() — asignación explícita a un solo objetivo ---")
    venta_1 = svc.register_sale(activo_id=activo_1_id, fecha="2026-04-01", monto_total_minor=300, objetivo_id=objetivo_A)
    caso("register_sale(): success=True", True, venta_1.success)
    caso("register_sale(): asignación 100% al objetivo elegido", objetivo_A, venta_1.data["objetivo_id"])
    caso("register_sale(): monto_asignado_minor = monto_total_minor completo", 300, venta_1.data["monto_asignado_minor"])

    venta_2 = svc.register_sale(activo_id=activo_1_id, fecha="2026-04-02", monto_total_minor=200, objetivo_id=objetivo_B)
    caso("register_sale() a un segundo objetivo: monto_asignado_minor correcto", 200, venta_2.data["monto_asignado_minor"])

    caso_excepcion(
        "register_sale() con objetivo_id inexistente lanza ObjetivoNotFoundError",
        ObjetivoNotFoundError,
        lambda: svc.register_sale(activo_id=activo_1_id, fecha="2026-04-03", monto_total_minor=100, objetivo_id=999999),
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

    manager.desconectar()

    print(f"\n--- Resumen: {casos_ok}/{casos_total} casos OK ---")


if __name__ == "__main__":
    main()
