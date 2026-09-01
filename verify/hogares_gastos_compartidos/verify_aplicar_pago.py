"""
verify/hogares_gastos_compartidos/verify_aplicar_pago.py

Verifica el mecanismo de pago parcial de gastos_compartidos (Tarea 9,
Parte A — docs/PROXIMOS_PASOS.md): SharedExpensesService.aplicar_pago(),
GastoCompartidoPagosRepository, GastosCompartidosRepository.
actualizar_monto_pendiente(), y el refactor de settle_expense() para que
internamente use aplicar_pago() en vez de duplicar la lógica de "marcar
saldado".

Cubre:
  - aplicar_pago() parcial: deja saldado=False, monto_pendiente_minor
    correcto, la fila en gasto_compartido_pagos con el monto real.
  - aplicar_pago() que completa el pendiente exacto: saldado=True.
  - aplicar_pago() con monto que supera el pendiente: se CLAMPEA al
    pendiente exacto (decisión de diseño, ver docstring de aplicar_pago()
    en services/shared_expenses_service.py) — confirma ajustado=True y que
    el monto realmente persistido es el pendiente, no el solicitado.
  - aplicar_pago() con tipo_pago='compensacion' sin transaccion_id: válido,
    se persiste igual (transaccion_id queda NULL).
  - aplicar_pago() sobre un gasto ya saldado: rechazado con
    GastoCompartidoYaSaldadoError — incluyendo con monto_aplicado_minor=0
    (inválido en sí mismo), para confirmar que el chequeo de estado corre
    ANTES que la validación de la forma del monto (orden corregido tras un
    bug real: settle_expense() sobre un gasto ya saldado calculaba
    monto_aplicado_minor=pendiente actual=0 y eso hacía salir el ValueError
    de "monto debe ser positivo" en vez de GastoCompartidoYaSaldadoError).
  - settle_expense() sigue funcionando igual que antes (ahora por dentro
    usa aplicar_pago()) — comparado explícitamente contra los mismos casos
    que ya cubría verify_shared_expenses_service.py antes de esta tarea:
    marca 'saldado', valida pertenencia al hogar correcto, valida gasto_id
    inexistente.
  - Atomicidad con rollback simulado: si el UPDATE de
    monto_pendiente_minor/estado falla a mitad de camino, el INSERT en
    gasto_compartido_pagos tampoco queda persistido.
  - get_net_balance() / vw_saldo_neto_hogar (corrección posterior a la
    Parte A original — ver comentario de la vista en db/schema.sql): un
    pago PARCIAL (que no llega a saldar el gasto) SÍ reduce el saldo neto
    devuelto, no solo un pago que lo completa — la vista suma
    monto_pendiente_minor, no monto_adeudado_minor. También confirma que
    un escenario de signos mixtos SIN ningún pago parcial aplicado da el
    mismo resultado que antes de este cambio (monto_pendiente_minor
    arranca igual a monto_adeudado_minor).

Correlo con:
    python verify/hogares_gastos_compartidos/verify_aplicar_pago.py
"""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from verify._dummy_db import crear_dummy_db
from db.database import DatabaseManager
from repositories.gastos_compartidos_repository import GastosCompartidosRepository
from services.shared_expenses_service import (
    SharedExpensesService,
    GastoCompartidoNotFoundError,
    GastoCompartidoYaSaldadoError,
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

    resultado_hogar = svc.create_hogar(nombre_creador_local="bruno", nombre_hogar="Hogar Pagos")
    hogar = resultado_hogar.entity_id
    svc.join_hogar(codigo_invitacion=resultado_hogar.data["codigo_invitacion"], nombre_local="martina")

    # ============================================================
    # aplicar_pago() — camino feliz, pago parcial
    # ============================================================
    print("--- aplicar_pago() — pago parcial ---")
    gasto_1 = svc.add_shared_expense(
        hogar_id=hogar, pagador="bruno", origen_tipo="transaccion", origen_id=5001,
        categoria_id=cat_id, monto_base_minor=10000, coeficiente_deuda=50.0, fecha="2026-04-01",
    )
    gasto_1_id = gasto_1.entity_id
    caso("gasto_1 arranca con monto_pendiente_minor = monto_adeudado_minor (5000)", 5000, gasto_1.data["monto_adeudado_minor"])

    pago_parcial = svc.aplicar_pago(
        gasto_id=gasto_1_id, hogar_id=hogar, monto_aplicado_minor=2000,
        fecha="2026-04-05", tipo_pago="transaccion", transaccion_id=None,
    )
    caso("aplicar_pago() parcial: success=True", True, pago_parcial.success)
    caso("aplicar_pago() parcial: saldado=False", False, pago_parcial.data["saldado"])
    caso("aplicar_pago() parcial: pendiente_minor = 5000 - 2000 = 3000", 3000, pago_parcial.data["pendiente_minor"])
    caso("aplicar_pago() parcial: monto_aplicado_minor real = 2000 (sin ajuste)", 2000, pago_parcial.data["monto_aplicado_minor"])
    caso("aplicar_pago() parcial: ajustado=False", False, pago_parcial.data["ajustado"])

    gasto_1_tras_parcial = svc._gastos_repo.obtener_por_id(gasto_1_id)
    caso("gasto_1 en DB: monto_pendiente_minor = 3000 tras el pago parcial", 3000, gasto_1_tras_parcial["monto_pendiente_minor"])
    caso("gasto_1 en DB: estado sigue 'pendiente'", "pendiente", gasto_1_tras_parcial["estado"])

    pagos_gasto_1 = svc._pagos_repo.listar_por_gasto(gasto_1_id)
    caso("gasto_compartido_pagos tiene 1 fila para gasto_1", 1, len(pagos_gasto_1))
    caso("la fila persistida tiene el monto real aplicado (2000)", 2000, pagos_gasto_1[0]["monto_aplicado_minor"])
    caso("la fila persistida tiene tipo_pago='transaccion'", "transaccion", pagos_gasto_1[0]["tipo_pago"])

    # ============================================================
    # aplicar_pago() — completa el pendiente exacto
    # ============================================================
    print("\n--- aplicar_pago() — completa el pendiente exacto ---")
    pago_completo = svc.aplicar_pago(
        gasto_id=gasto_1_id, hogar_id=hogar, monto_aplicado_minor=3000,
        fecha="2026-04-10", tipo_pago="transaccion",
    )
    caso("aplicar_pago() que completa el pendiente: saldado=True", True, pago_completo.data["saldado"])
    caso("aplicar_pago() que completa el pendiente: pendiente_minor = 0", 0, pago_completo.data["pendiente_minor"])

    gasto_1_saldado = svc._gastos_repo.obtener_por_id(gasto_1_id)
    caso("gasto_1 en DB: estado = 'saldado'", "saldado", gasto_1_saldado["estado"])
    caso("gasto_1 en DB: monto_pendiente_minor = 0", 0, gasto_1_saldado["monto_pendiente_minor"])

    # ============================================================
    # aplicar_pago() — sobre un gasto ya saldado
    # ============================================================
    print("\n--- aplicar_pago() — gasto ya saldado ---")
    caso_excepcion(
        "aplicar_pago() sobre un gasto 'saldado' lanza GastoCompartidoYaSaldadoError",
        GastoCompartidoYaSaldadoError,
        lambda: svc.aplicar_pago(
            gasto_id=gasto_1_id, hogar_id=hogar, monto_aplicado_minor=100, fecha="2026-04-11",
        ),
    )
    caso_excepcion(
        "aplicar_pago() sobre un gasto 'saldado' CON monto_aplicado_minor=0 (inválido en sí mismo) sigue "
        "lanzando GastoCompartidoYaSaldadoError, no el ValueError de 'monto debe ser positivo' — confirma "
        "el orden de validaciones corregido (estado del gasto ANTES que la forma de los parámetros sueltos): "
        "este es exactamente el escenario que reproducía el bug real de settle_expense() sobre un gasto ya "
        "saldado, donde el monto calculado (pendiente actual) también daba 0",
        GastoCompartidoYaSaldadoError,
        lambda: svc.aplicar_pago(
            gasto_id=gasto_1_id, hogar_id=hogar, monto_aplicado_minor=0, fecha="2026-04-11",
        ),
    )

    # ============================================================
    # aplicar_pago() — sobrepago (clamp)
    # ============================================================
    print("\n--- aplicar_pago() — sobrepago (clamp) ---")
    gasto_2 = svc.add_shared_expense(
        hogar_id=hogar, pagador="bruno", origen_tipo="transaccion", origen_id=5002,
        categoria_id=cat_id, monto_base_minor=4000, coeficiente_deuda=50.0, fecha="2026-04-02",
    )
    gasto_2_id = gasto_2.entity_id
    caso("gasto_2 arranca con pendiente = 2000", 2000, gasto_2.data["monto_adeudado_minor"])

    pago_sobrepago = svc.aplicar_pago(
        gasto_id=gasto_2_id, hogar_id=hogar, monto_aplicado_minor=5000,
        fecha="2026-04-06", tipo_pago="transaccion",
    )
    caso("sobrepago: se clampea, monto_aplicado_minor real = 2000 (no 5000)", 2000, pago_sobrepago.data["monto_aplicado_minor"])
    caso("sobrepago: monto_solicitado_minor conserva el valor pedido (5000)", 5000, pago_sobrepago.data["monto_solicitado_minor"])
    caso("sobrepago: ajustado=True", True, pago_sobrepago.data["ajustado"])
    caso("sobrepago: pendiente_minor = 0 (nunca queda negativo)", 0, pago_sobrepago.data["pendiente_minor"])
    caso("sobrepago: saldado=True", True, pago_sobrepago.data["saldado"])

    pagos_gasto_2 = svc._pagos_repo.listar_por_gasto(gasto_2_id)
    caso("la fila persistida para el sobrepago guarda el monto REAL (2000), no el solicitado", 2000, pagos_gasto_2[0]["monto_aplicado_minor"])

    # ============================================================
    # aplicar_pago() — tipo_pago='compensacion' sin transaccion_id
    # ============================================================
    print("\n--- aplicar_pago() — tipo_pago='compensacion' sin transaccion_id ---")
    gasto_3 = svc.add_shared_expense(
        hogar_id=hogar, pagador="bruno", origen_tipo="transaccion", origen_id=5003,
        categoria_id=cat_id, monto_base_minor=6000, coeficiente_deuda=50.0, fecha="2026-04-03",
    )
    gasto_3_id = gasto_3.entity_id

    pago_compensacion = svc.aplicar_pago(
        gasto_id=gasto_3_id, hogar_id=hogar, monto_aplicado_minor=1000,
        fecha="2026-04-07", tipo_pago="compensacion", notas="Ella compró el tomate, se descuenta",
    )
    caso("aplicar_pago(tipo_pago='compensacion') sin transaccion_id: success=True", True, pago_compensacion.success)
    caso("aplicar_pago(tipo_pago='compensacion'): transaccion_id queda None", None, pago_compensacion.data["transaccion_id"])

    pagos_gasto_3 = svc._pagos_repo.listar_por_gasto(gasto_3_id)
    caso("la fila persistida tiene tipo_pago='compensacion'", "compensacion", pagos_gasto_3[0]["tipo_pago"])
    caso("la fila persistida tiene transaccion_id NULL en DB", None, pagos_gasto_3[0]["transaccion_id"])
    caso("la fila persistida conserva las notas", "Ella compró el tomate, se descuenta", pagos_gasto_3[0]["notas"])

    caso_excepcion(
        "aplicar_pago() con tipo_pago inválido lanza ValueError",
        ValueError,
        lambda: svc.aplicar_pago(
            gasto_id=gasto_3_id, hogar_id=hogar, monto_aplicado_minor=500,
            fecha="2026-04-08", tipo_pago="no_existe",
        ),
    )
    caso_excepcion(
        "aplicar_pago() con monto_aplicado_minor <= 0 lanza ValueError",
        ValueError,
        lambda: svc.aplicar_pago(
            gasto_id=gasto_3_id, hogar_id=hogar, monto_aplicado_minor=0,
            fecha="2026-04-08", tipo_pago="transaccion",
        ),
    )
    caso_excepcion(
        "aplicar_pago() con gasto_id inexistente lanza GastoCompartidoNotFoundError",
        GastoCompartidoNotFoundError,
        lambda: svc.aplicar_pago(
            gasto_id=999999, hogar_id=hogar, monto_aplicado_minor=500,
            fecha="2026-04-08", tipo_pago="transaccion",
        ),
    )

    # ============================================================
    # settle_expense() — comportamiento observable sin cambios
    # ============================================================
    print("\n--- settle_expense() — sigue funcionando igual que antes (ahora usa aplicar_pago() por dentro) ---")
    gasto_4 = svc.add_shared_expense(
        hogar_id=hogar, pagador="bruno", origen_tipo="transaccion", origen_id=5004,
        categoria_id=cat_id, monto_base_minor=8000, coeficiente_deuda=25.0, fecha="2026-04-04",
    )
    gasto_4_id = gasto_4.entity_id
    caso("gasto_4 arranca con pendiente = 2000 (8000*25/100)", 2000, gasto_4.data["monto_adeudado_minor"])

    resultado_settle = svc.settle_expense(gasto_id=gasto_4_id, hogar_id=hogar)
    caso("settle_expense() camino feliz: success=True", True, resultado_settle.success)

    listado_saldados = svc.list_shared_expenses(hogar, estado="saldado")
    ids_saldados = [x["id"] for x in listado_saldados]
    caso("settle_expense() marca el gasto como 'saldado'", True, gasto_4_id in ids_saldados)

    gasto_4_db = svc._gastos_repo.obtener_por_id(gasto_4_id)
    caso("settle_expense(): monto_pendiente_minor queda en 0 (usó aplicar_pago() por dentro)", 0, gasto_4_db["monto_pendiente_minor"])

    pagos_gasto_4 = svc._pagos_repo.listar_por_gasto(gasto_4_id)
    caso("settle_expense() generó una fila en gasto_compartido_pagos (antes no existía ninguna)", 1, len(pagos_gasto_4))
    caso("settle_expense() generó el pago con tipo_pago='ajuste'", "ajuste", pagos_gasto_4[0]["tipo_pago"])
    caso("settle_expense() aplicó el pendiente completo (2000)", 2000, pagos_gasto_4[0]["monto_aplicado_minor"])

    # Segundo hogar, para reconfirmar la validación de pertenencia (mismo
    # caso que ya cubría verify_shared_expenses_service.py antes de esta tarea).
    hogar_2 = svc.create_hogar(nombre_creador_local="bruno", nombre_hogar="Hogar Pagos 2").entity_id
    gasto_otro_hogar = svc.add_shared_expense(
        hogar_id=hogar, pagador="bruno", origen_tipo="transaccion", origen_id=5005,
        categoria_id=cat_id, monto_base_minor=1000, coeficiente_deuda=50.0, fecha="2026-04-09",
    )
    caso_excepcion(
        "settle_expense() con un gasto que pertenece a OTRO hogar lanza GastoCompartidoNotFoundError",
        GastoCompartidoNotFoundError,
        lambda: svc.settle_expense(gasto_id=gasto_otro_hogar.entity_id, hogar_id=hogar_2),
    )
    caso_excepcion(
        "settle_expense() con un gasto_id inexistente lanza GastoCompartidoNotFoundError",
        GastoCompartidoNotFoundError,
        lambda: svc.settle_expense(gasto_id=999999, hogar_id=hogar),
    )
    caso_excepcion(
        "settle_expense() sobre un gasto ya saldado (llamado dos veces) lanza GastoCompartidoYaSaldadoError — "
        "antes marcar_saldado() era un no-op silencioso; luego, en la primera versión de aplicar_pago(), "
        "salía el ValueError equivocado ('monto debe ser positivo') porque settle_expense() le pasaba el "
        "pendiente actual (0, ya saldado) SIN chequear el estado antes — bug real encontrado por este mismo "
        "verify y corregido: settle_expense() valida 'ya saldado' explícitamente antes de calcular el monto, "
        "y aplicar_pago() valida existencia/estado del gasto antes que la forma de sus parámetros sueltos",
        GastoCompartidoYaSaldadoError,
        lambda: svc.settle_expense(gasto_id=gasto_4_id, hogar_id=hogar),
    )

    # ============================================================
    # get_net_balance() — un pago PARCIAL reduce el saldo neto
    # ============================================================
    print("\n--- get_net_balance() — pago parcial reduce el saldo neto (corrección posterior a monto_adeudado_minor) ---")
    hogar_balance = svc.create_hogar(nombre_creador_local="bruno", nombre_hogar="Hogar Balance Parcial").entity_id

    # Signos mixtos, igual que el escenario ya cubierto en
    # verify_shared_expenses_service.py ("Te deben") — SIN ningún pago
    # parcial todavía, para confirmar que el resultado no cambió.
    gasto_balance_a = svc.add_shared_expense(
        hogar_id=hogar_balance, pagador="bruno", origen_tipo="transaccion", origen_id=6001,
        categoria_id=cat_id, monto_base_minor=10000, coeficiente_deuda=50.0, fecha="2026-05-01",
    )
    gasto_balance_a_id = gasto_balance_a.entity_id
    gasto_balance_b = svc.add_shared_expense(
        hogar_id=hogar_balance, pagador="bruno", origen_tipo="compra_cuotas", origen_id=6002,
        categoria_id=cat_id, monto_base_minor=-10000, coeficiente_deuda=30.0, fecha="2026-05-02",
    )
    # gasto_balance_a.monto_adeudado_minor = 5000, gasto_balance_b.monto_adeudado_minor = -3000

    balance_sin_pagos = svc.get_net_balance(hogar_balance)
    caso(
        "get_net_balance() con signos mixtos y SIN pagos parciales: saldo_neto_minor = 2000 (5000 - 3000), "
        "mismo resultado que antes de sumar monto_pendiente_minor en vez de monto_adeudado_minor",
        2000,
        balance_sin_pagos["saldo_neto_minor"],
    )

    # Pago parcial (1500 de los 5000 pendientes) sobre gasto_balance_a — NO
    # lo salda, sigue 'pendiente'.
    svc.aplicar_pago(
        gasto_id=gasto_balance_a_id, hogar_id=hogar_balance, monto_aplicado_minor=1500,
        fecha="2026-05-05", tipo_pago="transaccion",
    )
    gasto_balance_a_tras_pago = svc._gastos_repo.obtener_por_id(gasto_balance_a_id)
    caso("gasto_balance_a sigue 'pendiente' tras el pago parcial (no lo saldó)", "pendiente", gasto_balance_a_tras_pago["estado"])
    caso("gasto_balance_a: monto_pendiente_minor = 5000 - 1500 = 3500", 3500, gasto_balance_a_tras_pago["monto_pendiente_minor"])

    balance_tras_pago_parcial = svc.get_net_balance(hogar_balance)
    caso(
        "get_net_balance() tras el pago parcial: saldo_neto_minor = 500 (3500 - 3000) — "
        "el pago parcial SÍ redujo el saldo neto, aunque el gasto sigue 'pendiente'",
        500,
        balance_tras_pago_parcial["saldo_neto_minor"],
    )
    caso(
        "el saldo neto bajó exactamente el monto aplicado (2000 -> 500, diferencia de 1500)",
        True,
        balance_sin_pagos["saldo_neto_minor"] - balance_tras_pago_parcial["saldo_neto_minor"] == 1500,
    )

    # ============================================================
    # Atomicidad con rollback simulado
    # ============================================================
    print("\n--- aplicar_pago() — atomicidad REAL con rollback simulado ---")
    gasto_5 = svc.add_shared_expense(
        hogar_id=hogar, pagador="bruno", origen_tipo="transaccion", origen_id=5006,
        categoria_id=cat_id, monto_base_minor=4000, coeficiente_deuda=50.0, fecha="2026-04-11",
    )
    gasto_5_id = gasto_5.entity_id

    pagos_antes_rollback = manager.fetchone("SELECT COUNT(*) AS n FROM gasto_compartido_pagos;")["n"]
    pendiente_antes_rollback = svc._gastos_repo.obtener_por_id(gasto_5_id)["monto_pendiente_minor"]

    metodo_original = GastosCompartidosRepository.actualizar_monto_pendiente

    def actualizar_que_falla(self, *args, **kwargs):
        raise RuntimeError("Fallo simulado en actualizar_monto_pendiente(), a mitad de la transacción")

    GastosCompartidosRepository.actualizar_monto_pendiente = actualizar_que_falla
    try:
        caso_excepcion(
            "aplicar_pago() propaga el fallo simulado de actualizar_monto_pendiente()",
            RuntimeError,
            lambda: svc.aplicar_pago(
                gasto_id=gasto_5_id, hogar_id=hogar, monto_aplicado_minor=500,
                fecha="2026-04-12", tipo_pago="transaccion",
            ),
        )
    finally:
        GastosCompartidosRepository.actualizar_monto_pendiente = metodo_original

    pagos_despues_rollback = manager.fetchone("SELECT COUNT(*) AS n FROM gasto_compartido_pagos;")["n"]
    pendiente_despues_rollback = svc._gastos_repo.obtener_por_id(gasto_5_id)["monto_pendiente_minor"]

    caso("rollback revierte el INSERT en gasto_compartido_pagos (no queda huérfano)", pagos_antes_rollback, pagos_despues_rollback)
    caso("rollback: monto_pendiente_minor de gasto_5 no cambió", pendiente_antes_rollback, pendiente_despues_rollback)

    manager.desconectar()

    print(f"\n--- Resumen: {casos_ok}/{casos_total} casos OK ---")


if __name__ == "__main__":
    main()
