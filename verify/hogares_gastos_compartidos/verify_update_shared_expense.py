"""
verify/hogares_gastos_compartidos/verify_update_shared_expense.py

Verifica SharedExpensesService.update_shared_expense() extendido para la
edición inline de ui/screens/deudas_y_compartidos.py: además de
descripcion, ahora acepta monto_base_minor, coeficiente_deuda y fecha
(sentinel NO_CAMBIAR = no tocar).

Qué se prueba y por qué (ventana de corrección temprana, CLAUDE.md §4):
- Monto base y coeficiente se editan en un gasto 'pendiente' SIN pagos, y
  al cambiar cualquiera de los dos se recalculan en la misma transacción
  monto_adeudado_minor = round(monto_base_minor * coeficiente_deuda / 100)
  y monto_pendiente_minor (= el adeudado nuevo, porque no hay pagos).
- En un gasto 'saldado' cambiar monto/coeficiente lanza SharedExpensesError
  y no escribe nada — pero descripción y fecha SÍ se editan (son
  descriptivas, no alimentan ningún cálculo).
- Un gasto 'pendiente' con un pago PARCIAL también bloquea monto/
  coeficiente: el pago es una dependencia con estado propio (mismo
  criterio que delete_shared_expense()).
- Validaciones de forma (fecha, monto 0, coeficiente fuera de rango) y de
  pertenencia (gasto inexistente / de otro hogar).

Correlo con:
    python verify/hogares_gastos_compartidos/verify_update_shared_expense.py
"""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from verify._dummy_db import crear_dummy_db
from db.database import DatabaseManager
from services.shared_expenses_service import (
    GastoCompartidoNotFoundError,
    SharedExpensesError,
    SharedExpensesService,
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
        except tipo_esperado as e:
            casos_ok += 1
            print(f"✅ {descripcion} — lanzó {tipo_esperado.__name__} como se esperaba ({e})")
        except Exception as e:
            print(f"❌ {descripcion} — esperaba {tipo_esperado.__name__}, se lanzó {type(e).__name__}: {e!r}")

    db_path = crear_dummy_db()
    print(f"Dummy DB creada en: {db_path}\n")

    manager = DatabaseManager(db_path=db_path)
    manager.inicializar()
    svc = SharedExpensesService(manager)

    cat_id = manager.fetchone("SELECT id FROM categorias WHERE tipo = 'egreso' LIMIT 1;")["id"]
    hogar = svc.create_hogar(nombre_creador_local="bruno", nombre_hogar="Hogar Update GC").entity_id
    hogar_2 = svc.create_hogar(nombre_creador_local="otra", nombre_hogar="Otro hogar").entity_id

    def nuevo_gasto(origen_id: int, monto_base_minor: int, fecha: str) -> int:
        # origen_id/monto/fecha distintos en cada gasto: add_shared_expense()
        # bloquea origen repetido, y el repositorio bloquea gastos idénticos
        # creados con menos de 5 segundos de diferencia.
        return svc.add_shared_expense(
            hogar_id=hogar, pagador="bruno", origen_tipo="transaccion", origen_id=origen_id,
            categoria_id=cat_id, monto_base_minor=monto_base_minor, coeficiente_deuda=50.0,
            fecha=fecha, descripcion="Supermercado",
        ).entity_id

    def gasto(gasto_id: int):
        return svc._gastos_repo.obtener_por_id(gasto_id)

    # ============================================================
    print("--- Pendiente sin pagos: editar monto base recalcula adeudado y pendiente ---")
    # ============================================================
    g1 = nuevo_gasto(9001, 10000, "2026-02-01")
    caso("arranque: monto_adeudado_minor = round(10000 * 50 / 100) = 5000", 5000, gasto(g1)["monto_adeudado_minor"])

    res = svc.update_shared_expense(gasto_id=g1, hogar_id=hogar, monto_base_minor=30000)
    fila = gasto(g1)
    caso("update_shared_expense(monto_base_minor=...) devuelve success=True", True, res.success)
    caso("monto_base_minor = 30000", 30000, fila["monto_base_minor"])
    caso("monto_adeudado_minor recalculado = round(30000 * 50 / 100) = 15000", 15000, fila["monto_adeudado_minor"])
    caso("monto_pendiente_minor = adeudado nuevo (no hay pagos) = 15000", 15000, fila["monto_pendiente_minor"])
    caso("el estado sigue 'pendiente'", "pendiente", fila["estado"])
    caso("el coeficiente no se tocó", 50.0, fila["coeficiente_deuda"])

    # ============================================================
    print("\n--- Pendiente sin pagos: editar coeficiente recalcula con el monto base actual ---")
    # ============================================================
    res = svc.update_shared_expense(gasto_id=g1, hogar_id=hogar, coeficiente_deuda=33.3)
    fila = gasto(g1)
    caso("update_shared_expense(coeficiente_deuda=...) devuelve success=True", True, res.success)
    caso("coeficiente_deuda = 33.3", 33.3, fila["coeficiente_deuda"])
    caso("monto_adeudado_minor = round(30000 * 33.3 / 100) = 9990", 9990, fila["monto_adeudado_minor"])
    caso("monto_pendiente_minor = 9990", 9990, fila["monto_pendiente_minor"])

    res = svc.update_shared_expense(gasto_id=g1, hogar_id=hogar, monto_base_minor=20000, coeficiente_deuda=100.0)
    fila = gasto(g1)
    caso("monto + coeficiente juntos: adeudado = round(20000 * 100 / 100) = 20000", 20000, fila["monto_adeudado_minor"])
    caso("…y pendiente = 20000", 20000, fila["monto_pendiente_minor"])

    caso(
        "update_shared_expense() sin campos devuelve success=False",
        False,
        svc.update_shared_expense(gasto_id=g1, hogar_id=hogar).success,
    )

    # ============================================================
    print("\n--- Saldado: monto y coeficiente bloqueados ---")
    # ============================================================
    g2 = nuevo_gasto(9002, 8000, "2026-02-02")
    svc.settle_expense(gasto_id=g2, hogar_id=hogar)
    caso("arranque: el gasto quedó 'saldado'", "saldado", gasto(g2)["estado"])

    caso_excepcion(
        "editar monto_base_minor de un gasto 'saldado' → SharedExpensesError",
        SharedExpensesError,
        lambda: svc.update_shared_expense(gasto_id=g2, hogar_id=hogar, monto_base_minor=9000),
    )
    caso_excepcion(
        "editar coeficiente_deuda de un gasto 'saldado' → SharedExpensesError",
        SharedExpensesError,
        lambda: svc.update_shared_expense(gasto_id=g2, hogar_id=hogar, coeficiente_deuda=25.0),
    )
    fila = gasto(g2)
    caso("el monto base quedó sin tocar", 8000, fila["monto_base_minor"])
    caso("el adeudado quedó sin tocar", 4000, fila["monto_adeudado_minor"])

    caso_excepcion(
        "descripción + monto en la misma llamada sobre 'saldado' → SharedExpensesError",
        SharedExpensesError,
        lambda: svc.update_shared_expense(gasto_id=g2, hogar_id=hogar, descripcion="No debería", monto_base_minor=9000),
    )
    caso("…y la descripción tampoco se escribió a medias", "Supermercado", gasto(g2)["descripcion"])

    caso(
        "pasar el MISMO monto base actual sobre 'saldado' no es un cambio (success=False, sin excepción)",
        False,
        svc.update_shared_expense(gasto_id=g2, hogar_id=hogar, monto_base_minor=8000).success,
    )

    # ============================================================
    print("\n--- Saldado: descripción y fecha SÍ se editan ---")
    # ============================================================
    res = svc.update_shared_expense(gasto_id=g2, hogar_id=hogar, descripcion="Supermercado Coto")
    caso("editar descripción de un gasto 'saldado' devuelve success=True", True, res.success)
    caso("la descripción nueva quedó guardada", "Supermercado Coto", gasto(g2)["descripcion"])

    res = svc.update_shared_expense(gasto_id=g2, hogar_id=hogar, fecha="2026-02-15")
    caso("editar fecha de un gasto 'saldado' devuelve success=True", True, res.success)
    caso("la fecha nueva quedó guardada", "2026-02-15", gasto(g2)["fecha"])
    caso("editar descripción/fecha no cambió el estado", "saldado", gasto(g2)["estado"])

    svc.update_shared_expense(gasto_id=g2, hogar_id=hogar, descripcion=None)
    caso("descripcion=None limpia la descripción", None, gasto(g2)["descripcion"])

    # ============================================================
    print("\n--- Pendiente con un pago parcial: monto y coeficiente también bloqueados ---")
    # ============================================================
    g3 = nuevo_gasto(9003, 12000, "2026-02-03")
    svc.aplicar_pago(gasto_id=g3, hogar_id=hogar, monto_aplicado_minor=1000, fecha="2026-02-10")
    caso("arranque: sigue 'pendiente' con pendiente = 6000 - 1000 = 5000", ("pendiente", 5000),
         (gasto(g3)["estado"], gasto(g3)["monto_pendiente_minor"]))

    caso_excepcion(
        "editar monto_base_minor con un pago parcial registrado → SharedExpensesError",
        SharedExpensesError,
        lambda: svc.update_shared_expense(gasto_id=g3, hogar_id=hogar, monto_base_minor=2000),
    )
    caso("el pendiente quedó sin tocar (no se pisó el pago)", 5000, gasto(g3)["monto_pendiente_minor"])
    caso(
        "la fecha SÍ se puede editar con pagos registrados",
        True,
        svc.update_shared_expense(gasto_id=g3, hogar_id=hogar, fecha="2026-02-04").success,
    )

    # ============================================================
    print("\n--- Validaciones ---")
    # ============================================================
    caso_excepcion("fecha con formato inválido → ValueError", ValueError,
                   lambda: svc.update_shared_expense(gasto_id=g1, hogar_id=hogar, fecha="15/02/2026"))
    caso_excepcion("monto_base_minor = 0 → ValueError", ValueError,
                   lambda: svc.update_shared_expense(gasto_id=g1, hogar_id=hogar, monto_base_minor=0))
    caso_excepcion("coeficiente_deuda = 0 → ValueError", ValueError,
                   lambda: svc.update_shared_expense(gasto_id=g1, hogar_id=hogar, coeficiente_deuda=0.0))
    caso_excepcion("coeficiente_deuda > 100 → ValueError", ValueError,
                   lambda: svc.update_shared_expense(gasto_id=g1, hogar_id=hogar, coeficiente_deuda=150.0))
    caso("tras los rechazos, g1 sigue con adeudado 20000", 20000, gasto(g1)["monto_adeudado_minor"])
    caso_excepcion("gasto_id inexistente → GastoCompartidoNotFoundError", GastoCompartidoNotFoundError,
                   lambda: svc.update_shared_expense(gasto_id=999999, hogar_id=hogar, descripcion="X"))
    caso_excepcion("gasto de OTRO hogar → GastoCompartidoNotFoundError", GastoCompartidoNotFoundError,
                   lambda: svc.update_shared_expense(gasto_id=g1, hogar_id=hogar_2, descripcion="X"))

    manager.desconectar()

    print(f"\n--- Resumen: {casos_ok}/{casos_total} casos OK ---")


if __name__ == "__main__":
    main()
