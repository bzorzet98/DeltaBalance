"""
verify/hogares_gastos_compartidos/verify_shared_expenses_service.py

Verifica SharedExpensesService (services/shared_expenses_service.py) —
creado desde cero en Fase 2, bloque HOGARES / GASTOS COMPARTIDOS paso 2,
sin comportamiento previo que replicar.

Cubre: create_hogar() (formato del código de invitación, atomicidad
hogar+miembro), join_hogar() (camino feliz, código inválido, miembro
duplicado), get_suggested_coefficient() (con y sin default configurado),
add_shared_expense() (camino feliz, todas las validaciones — incluida la
corrección de diseño: coeficiente_deuda SIEMPRE positivo por el CHECK de
schema.sql, el signo de monto_adeudado_minor lo hereda monto_base_minor,
no el coeficiente — y el bloqueo de duplicado por mismo origen),
settle_expense() (con validación de pertenencia al hogar correcto), y
get_net_balance() con los tres mensajes posibles sobre escenarios de
signos mixtos.

Correlo con:
    python verify/hogares_gastos_compartidos/verify_shared_expenses_service.py
"""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from verify._dummy_db import crear_dummy_db
from db.database import DatabaseManager
from services.shared_expenses_service import (
    SharedExpensesService,
    SharedExpensesError,
    HogarNotFoundError,
    MiembroNotFoundError,
    GastoCompartidoNotFoundError,
    CodigoInvitacionInvalidoError,
    MiembroYaExisteError,
    GastoCompartidoDuplicadoError,
    CODIGO_ALFABETO,
    CODIGO_LONGITUD,
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
    svc = SharedExpensesService(manager)

    cat_id = manager.fetchone("SELECT id FROM categorias WHERE tipo = 'egreso' LIMIT 1;")["id"]

    # ============================================================
    # create_hogar()
    # ============================================================
    print("--- create_hogar() ---")
    resultado_hogar_favor = svc.create_hogar(nombre_creador_local="bruno", nombre_hogar="Hogar Favor")
    caso("create_hogar() devuelve success=True", True, resultado_hogar_favor.success)
    caso("create_hogar() devuelve entity_id numérico", True, isinstance(resultado_hogar_favor.entity_id, int))
    hogar_favor = resultado_hogar_favor.entity_id
    codigo_favor = resultado_hogar_favor.data["codigo_invitacion"]

    caso("create_hogar() genera un código de 10 caracteres", CODIGO_LONGITUD, len(codigo_favor))
    caso(
        "create_hogar() genera un código solo con mayúsculas y dígitos (CODIGO_ALFABETO)",
        True,
        all(c in CODIGO_ALFABETO for c in codigo_favor),
    )

    miembros_favor = svc.list_miembros(hogar_favor)
    caso(
        "create_hogar() es atómico: el creador ('bruno') ya es miembro del hogar",
        True,
        "bruno" in [m["usuario_local"] for m in miembros_favor],
    )

    caso_excepcion(
        "create_hogar() con nombre_creador_local vacío lanza ValueError",
        ValueError,
        lambda: svc.create_hogar(nombre_creador_local="   "),
    )

    # ============================================================
    # join_hogar()
    # ============================================================
    print("\n--- join_hogar() ---")
    resultado_join = svc.join_hogar(codigo_invitacion=codigo_favor, nombre_local="martina", porcentaje_default=40.0)
    caso("join_hogar() camino feliz: success=True", True, resultado_join.success)
    caso("join_hogar() camino feliz: entity_id = hogar_favor", hogar_favor, resultado_join.entity_id)

    miembros_favor_v2 = svc.list_miembros(hogar_favor)
    caso("join_hogar() agrega a martina como miembro", True, "martina" in [m["usuario_local"] for m in miembros_favor_v2])

    caso_excepcion(
        "join_hogar() con código inválido lanza CodigoInvitacionInvalidoError",
        CodigoInvitacionInvalidoError,
        lambda: svc.join_hogar(codigo_invitacion="NOEXISTE12", nombre_local="alguien"),
    )

    caso_excepcion(
        "join_hogar() con un usuario_local que ya es miembro lanza MiembroYaExisteError",
        MiembroYaExisteError,
        lambda: svc.join_hogar(codigo_invitacion=codigo_favor, nombre_local="martina"),
    )

    # ============================================================
    # get_suggested_coefficient()
    # ============================================================
    print("\n--- get_suggested_coefficient() ---")
    caso(
        "get_suggested_coefficient() de martina (tiene default) devuelve 40.0",
        40.0,
        svc.get_suggested_coefficient(hogar_favor, "martina"),
    )
    caso(
        "get_suggested_coefficient() de bruno (sin default, se agregó sin porcentaje_default) devuelve None",
        None,
        svc.get_suggested_coefficient(hogar_favor, "bruno"),
    )
    caso_excepcion(
        "get_suggested_coefficient() con un miembro inexistente lanza MiembroNotFoundError",
        MiembroNotFoundError,
        lambda: svc.get_suggested_coefficient(hogar_favor, "no_existe"),
    )

    # ============================================================
    # add_shared_expense() — camino feliz + validaciones
    # ============================================================
    print("\n--- add_shared_expense() — camino feliz ---")
    gasto_1 = svc.add_shared_expense(
        hogar_id=hogar_favor, pagador="bruno", origen_tipo="transaccion", origen_id=2001,
        categoria_id=cat_id, monto_base_minor=10000, coeficiente_deuda=50.0, fecha="2026-01-05",
    )
    caso("add_shared_expense() camino feliz: success=True", True, gasto_1.success)
    caso("add_shared_expense() calcula monto_adeudado_minor = round(10000*50/100) = 5000", 5000, gasto_1.data["monto_adeudado_minor"])
    gasto_1_id = gasto_1.entity_id

    print("\n--- add_shared_expense() — validaciones ---")
    caso_excepcion(
        "add_shared_expense() con hogar_id inexistente lanza HogarNotFoundError",
        HogarNotFoundError,
        lambda: svc.add_shared_expense(
            hogar_id=999999, pagador="bruno", origen_tipo="transaccion", origen_id=9001,
            categoria_id=cat_id, monto_base_minor=1000, coeficiente_deuda=50.0, fecha="2026-01-01",
        ),
    )
    caso_excepcion(
        "add_shared_expense() con pagador que no es miembro lanza MiembroNotFoundError",
        MiembroNotFoundError,
        lambda: svc.add_shared_expense(
            hogar_id=hogar_favor, pagador="no_es_miembro", origen_tipo="transaccion", origen_id=9002,
            categoria_id=cat_id, monto_base_minor=1000, coeficiente_deuda=50.0, fecha="2026-01-01",
        ),
    )
    caso_excepcion(
        "add_shared_expense() con categoria_id inexistente lanza SharedExpensesError",
        SharedExpensesError,
        lambda: svc.add_shared_expense(
            hogar_id=hogar_favor, pagador="bruno", origen_tipo="transaccion", origen_id=9003,
            categoria_id=999999, monto_base_minor=1000, coeficiente_deuda=50.0, fecha="2026-01-01",
        ),
    )
    caso_excepcion(
        "add_shared_expense() con monto_base_minor=0 lanza ValueError",
        ValueError,
        lambda: svc.add_shared_expense(
            hogar_id=hogar_favor, pagador="bruno", origen_tipo="transaccion", origen_id=9004,
            categoria_id=cat_id, monto_base_minor=0, coeficiente_deuda=50.0, fecha="2026-01-01",
        ),
    )
    caso_excepcion(
        "add_shared_expense() con coeficiente_deuda=0 lanza ValueError (debe ser > 0)",
        ValueError,
        lambda: svc.add_shared_expense(
            hogar_id=hogar_favor, pagador="bruno", origen_tipo="transaccion", origen_id=9005,
            categoria_id=cat_id, monto_base_minor=1000, coeficiente_deuda=0, fecha="2026-01-01",
        ),
    )
    caso_excepcion(
        "add_shared_expense() con coeficiente_deuda=150 lanza ValueError (debe ser <= 100)",
        ValueError,
        lambda: svc.add_shared_expense(
            hogar_id=hogar_favor, pagador="bruno", origen_tipo="transaccion", origen_id=9006,
            categoria_id=cat_id, monto_base_minor=1000, coeficiente_deuda=150.0, fecha="2026-01-01",
        ),
    )
    caso_excepcion(
        "add_shared_expense() con coeficiente_deuda NEGATIVO lanza ValueError — el coeficiente "
        "nunca invierte el signo, eso lo hace monto_base_minor (ver corrección de diseño)",
        ValueError,
        lambda: svc.add_shared_expense(
            hogar_id=hogar_favor, pagador="bruno", origen_tipo="transaccion", origen_id=9007,
            categoria_id=cat_id, monto_base_minor=1000, coeficiente_deuda=-30.0, fecha="2026-01-01",
        ),
    )
    caso_excepcion(
        "add_shared_expense() con origen_tipo+origen_id duplicado (mismo que gasto_1) lanza GastoCompartidoDuplicadoError",
        GastoCompartidoDuplicadoError,
        lambda: svc.add_shared_expense(
            hogar_id=hogar_favor, pagador="bruno", origen_tipo="transaccion", origen_id=2001,
            categoria_id=cat_id, monto_base_minor=500, coeficiente_deuda=50.0, fecha="2026-01-06",
        ),
    )

    print("\n--- add_shared_expense() — monto_base_minor negativo -> monto_adeudado_minor negativo ---")
    gasto_3 = svc.add_shared_expense(
        hogar_id=hogar_favor, pagador="bruno", origen_tipo="compra_cuotas", origen_id=2002,
        categoria_id=cat_id, monto_base_minor=-10000, coeficiente_deuda=30.0, fecha="2026-01-10",
        descripcion="Reintegro superó el monto de la cuota",
    )
    caso(
        "monto_base_minor negativo con coeficiente_deuda positivo (30) da monto_adeudado_minor = round(-10000*30/100) = -3000",
        -3000,
        gasto_3.data["monto_adeudado_minor"],
    )
    gasto_3_id = gasto_3.entity_id

    gasto_4 = svc.add_shared_expense(
        hogar_id=hogar_favor, pagador="bruno", origen_tipo="cuota_credito", origen_id=2003,
        categoria_id=cat_id, monto_base_minor=6000, coeficiente_deuda=20.0, fecha="2026-01-15",
    )
    caso("gasto_4: monto_adeudado_minor = round(6000*20/100) = 1200", 1200, gasto_4.data["monto_adeudado_minor"])
    gasto_4_id = gasto_4.entity_id

    # ============================================================
    # list_shared_expenses()
    # ============================================================
    print("\n--- list_shared_expenses() ---")
    listado_favor = svc.list_shared_expenses(hogar_favor)
    caso(
        "list_shared_expenses(hogar_favor) trae los 3 gastos creados",
        True,
        all(g in [x["id"] for x in listado_favor] for g in (gasto_1_id, gasto_3_id, gasto_4_id)),
    )
    caso(
        "list_shared_expenses() usa listar_enriquecida() — trae category_name",
        True,
        all(row["category_name"] is not None for row in listado_favor),
    )

    listado_bruno = svc.list_shared_expenses(hogar_favor, pagador="bruno")
    caso("list_shared_expenses(pagador='bruno') trae los 3 gastos (todos pagados por bruno)", 3, len(listado_bruno))

    # ============================================================
    # get_net_balance() — escenario 'Te deben' (hogar_favor, antes de saldar nada)
    # ============================================================
    print("\n--- get_net_balance() — 'Te deben' ---")
    # Pendientes de hogar_favor: gasto_1 (5000) + gasto_3 (-3000) + gasto_4 (1200) = 3200
    balance_favor = svc.get_net_balance(hogar_favor)
    caso("get_net_balance(hogar_favor): saldo_neto_minor = 3200 (5000 - 3000 + 1200)", 3200, balance_favor["saldo_neto_minor"])
    caso("get_net_balance(hogar_favor): mensaje = 'Te deben $32.00.'", "Te deben $32.00.", balance_favor["mensaje"])

    caso_excepcion(
        "get_net_balance() con hogar_id inexistente lanza HogarNotFoundError",
        HogarNotFoundError,
        lambda: svc.get_net_balance(999999),
    )

    # ============================================================
    # settle_expense() — camino feliz + validación de pertenencia
    # ============================================================
    print("\n--- settle_expense() ---")
    resultado_settle = svc.settle_expense(gasto_id=gasto_4_id, hogar_id=hogar_favor)
    caso("settle_expense() camino feliz: success=True", True, resultado_settle.success)

    listado_saldados = svc.list_shared_expenses(hogar_favor, estado="saldado")
    caso("settle_expense() marca el gasto como 'saldado'", [gasto_4_id], [x["id"] for x in listado_saldados])

    # Crear un segundo hogar para probar que settle_expense() valida pertenencia real
    resultado_hogar_contra = svc.create_hogar(nombre_creador_local="bruno", nombre_hogar="Hogar Contra")
    hogar_contra = resultado_hogar_contra.entity_id

    caso_excepcion(
        "settle_expense() con un gasto que pertenece a OTRO hogar lanza GastoCompartidoNotFoundError",
        GastoCompartidoNotFoundError,
        lambda: svc.settle_expense(gasto_id=gasto_1_id, hogar_id=hogar_contra),
    )
    caso_excepcion(
        "settle_expense() con un gasto_id inexistente lanza GastoCompartidoNotFoundError",
        GastoCompartidoNotFoundError,
        lambda: svc.settle_expense(gasto_id=999999, hogar_id=hogar_favor),
    )

    # ============================================================
    # get_net_balance() — escenario 'Debés' (hogar_contra)
    # ============================================================
    print("\n--- get_net_balance() — 'Debés' ---")
    svc.add_shared_expense(
        hogar_id=hogar_contra, pagador="bruno", origen_tipo="transaccion", origen_id=3001,
        categoria_id=cat_id, monto_base_minor=-10000, coeficiente_deuda=25.0, fecha="2026-02-01",
    )
    balance_contra = svc.get_net_balance(hogar_contra)
    caso("get_net_balance(hogar_contra): saldo_neto_minor = -2500", -2500, balance_contra["saldo_neto_minor"])
    caso("get_net_balance(hogar_contra): mensaje = 'Debés $25.00.'", "Debés $25.00.", balance_contra["mensaje"])

    # ============================================================
    # get_net_balance() — escenario 'Están a mano' (hogar_mano, signos mixtos que cancelan)
    # ============================================================
    print("\n--- get_net_balance() — 'Están a mano' ---")
    resultado_hogar_mano = svc.create_hogar(nombre_creador_local="bruno", nombre_hogar="Hogar Mano")
    hogar_mano = resultado_hogar_mano.entity_id
    svc.add_shared_expense(
        hogar_id=hogar_mano, pagador="bruno", origen_tipo="transaccion", origen_id=4001,
        categoria_id=cat_id, monto_base_minor=10000, coeficiente_deuda=30.0, fecha="2026-03-01",
    )
    svc.add_shared_expense(
        hogar_id=hogar_mano, pagador="bruno", origen_tipo="compra_cuotas", origen_id=4002,
        categoria_id=cat_id, monto_base_minor=-10000, coeficiente_deuda=30.0, fecha="2026-03-02",
    )
    balance_mano = svc.get_net_balance(hogar_mano)
    caso("get_net_balance(hogar_mano): saldo_neto_minor = 0 (3000 - 3000)", 0, balance_mano["saldo_neto_minor"])
    caso("get_net_balance(hogar_mano): mensaje = 'Están a mano.'", "Están a mano.", balance_mano["mensaje"])

    manager.desconectar()

    print(f"\n--- Resumen: {casos_ok}/{casos_total} casos OK ---")


if __name__ == "__main__":
    main()
