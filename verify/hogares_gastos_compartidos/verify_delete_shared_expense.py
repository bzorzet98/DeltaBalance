"""
verify/hogares_gastos_compartidos/verify_delete_shared_expense.py

Verifica SharedExpensesService.delete_shared_expense() /
GastosCompartidosRepository.eliminar() (Tarea 9, Parte B —
docs/PROXIMOS_PASOS.md): ventana de corrección temprana (CLAUDE.md §4)
aplicada a gastos_compartidos, mismo criterio que DebtsService.delete_debt()
— borrado real solo si el gasto no tiene ninguna dependencia con estado
propio generado (pagos en gasto_compartido_pagos).

Cubre:
  - delete_shared_expense() sin ningún pago registrado: DELETE físico
    permitido, la fila desaparece de verdad.
  - delete_shared_expense() con al menos un pago parcial (vía
    aplicar_pago()): bloqueado con SharedExpensesError, el gasto sigue
    existiendo intacto.
  - delete_shared_expense() sobre un gasto YA SALDADO (que por lo tanto
    siempre tiene al menos un pago, ver aplicar_pago()): también bloqueado
    — confirma que lo que bloquea es la dependencia (pagos), no el estado.
  - delete_shared_expense() con gasto_id inexistente: GastoCompartidoNotFoundError.
  - delete_shared_expense() con un gasto que pertenece a OTRO hogar: rechazado
    (misma validación de pertenencia que settle_expense()/aplicar_pago()).
  - GastosCompartidosRepository.eliminar() en sí (DELETE físico directo,
    sin la validación de negocio — esa vive en el service).
  - update_shared_expense() (agregado junto con esta tarea — no existía
    ningún método de service para editar la descripción todavía, aunque
    el repositorio ya lo soportaba): cambia la descripción, la limpia con
    None, y valida pertenencia al hogar igual que el resto.

Correlo con:
    python verify/hogares_gastos_compartidos/verify_delete_shared_expense.py
"""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from verify._dummy_db import crear_dummy_db
from db.database import DatabaseManager
from services.shared_expenses_service import (
    SharedExpensesService,
    SharedExpensesError,
    GastoCompartidoNotFoundError,
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
    hogar = svc.create_hogar(nombre_creador_local="bruno", nombre_hogar="Hogar Delete GC").entity_id

    # ============================================================
    # delete_shared_expense() — sin pagos: permitido, DELETE físico real
    # ============================================================
    print("--- delete_shared_expense() — sin pagos registrados ---")
    gasto_sin_pagos = svc.add_shared_expense(
        hogar_id=hogar, pagador="bruno", origen_tipo="transaccion", origen_id=8001,
        categoria_id=cat_id, monto_base_minor=2000, coeficiente_deuda=50.0, fecha="2026-01-01",
    ).entity_id

    resultado_delete = svc.delete_shared_expense(gasto_id=gasto_sin_pagos, hogar_id=hogar)
    caso("delete_shared_expense() sin pagos: success=True", True, resultado_delete.success)
    caso(
        "delete_shared_expense() elimina la fila de verdad (obtener_por_id devuelve None)",
        None,
        svc._gastos_repo.obtener_por_id(gasto_sin_pagos),
    )

    # ============================================================
    # delete_shared_expense() — con un pago parcial: bloqueado
    # ============================================================
    print("\n--- delete_shared_expense() — con un pago parcial registrado ---")
    gasto_con_pago = svc.add_shared_expense(
        hogar_id=hogar, pagador="bruno", origen_tipo="transaccion", origen_id=8002,
        categoria_id=cat_id, monto_base_minor=10000, coeficiente_deuda=50.0, fecha="2026-01-02",
    ).entity_id
    svc.aplicar_pago(gasto_id=gasto_con_pago, hogar_id=hogar, monto_aplicado_minor=1000, fecha="2026-01-05")

    caso_excepcion(
        "delete_shared_expense() con un pago parcial registrado lanza SharedExpensesError",
        SharedExpensesError,
        lambda: svc.delete_shared_expense(gasto_id=gasto_con_pago, hogar_id=hogar),
    )
    caso(
        "el gasto con pago parcial SIGUE existiendo tras el intento de borrado",
        True,
        svc._gastos_repo.obtener_por_id(gasto_con_pago) is not None,
    )

    # ============================================================
    # delete_shared_expense() — gasto ya saldado (siempre tiene >=1 pago)
    # ============================================================
    print("\n--- delete_shared_expense() — gasto ya saldado (settle_expense) también bloqueado ---")
    gasto_saldado = svc.add_shared_expense(
        hogar_id=hogar, pagador="bruno", origen_tipo="transaccion", origen_id=8003,
        categoria_id=cat_id, monto_base_minor=4000, coeficiente_deuda=50.0, fecha="2026-01-03",
    ).entity_id
    svc.settle_expense(gasto_id=gasto_saldado, hogar_id=hogar)

    caso_excepcion(
        "delete_shared_expense() sobre un gasto 'saldado' (settle_expense siempre deja un pago) "
        "lanza SharedExpensesError — lo que bloquea es la dependencia (pagos), no el estado en sí",
        SharedExpensesError,
        lambda: svc.delete_shared_expense(gasto_id=gasto_saldado, hogar_id=hogar),
    )

    # ============================================================
    # delete_shared_expense() — gasto_id inexistente / hogar equivocado
    # ============================================================
    print("\n--- delete_shared_expense() — identidad/pertenencia ---")
    caso_excepcion(
        "delete_shared_expense() con gasto_id inexistente lanza GastoCompartidoNotFoundError",
        GastoCompartidoNotFoundError,
        lambda: svc.delete_shared_expense(gasto_id=999999, hogar_id=hogar),
    )

    hogar_2 = svc.create_hogar(nombre_creador_local="bruno", nombre_hogar="Hogar Delete GC 2").entity_id
    gasto_otro_hogar = svc.add_shared_expense(
        hogar_id=hogar, pagador="bruno", origen_tipo="transaccion", origen_id=8004,
        categoria_id=cat_id, monto_base_minor=1500, coeficiente_deuda=50.0, fecha="2026-01-04",
    ).entity_id
    caso_excepcion(
        "delete_shared_expense() con un gasto que pertenece a OTRO hogar lanza GastoCompartidoNotFoundError",
        GastoCompartidoNotFoundError,
        lambda: svc.delete_shared_expense(gasto_id=gasto_otro_hogar, hogar_id=hogar_2),
    )
    caso(
        "el gasto de 'otro hogar' rechazado SIGUE existiendo (no se borró el equivocado)",
        True,
        svc._gastos_repo.obtener_por_id(gasto_otro_hogar) is not None,
    )

    # ============================================================
    # GastosCompartidosRepository.eliminar() directo (sin la validación del service)
    # ============================================================
    print("\n--- GastosCompartidosRepository.eliminar() — DELETE físico directo, sin validación de negocio ---")
    gasto_repo_directo = svc._gastos_repo.crear(
        hogar_id=hogar, pagador="bruno", origen_tipo="transaccion", origen_id=8005,
        categoria_id=cat_id, monto_base_minor=999, coeficiente_deuda=50.0,
        monto_adeudado_minor=500, fecha="2026-01-06",
    )
    svc._gastos_repo.eliminar(gasto_repo_directo)
    caso("repo.eliminar() borra la fila directamente", None, svc._gastos_repo.obtener_por_id(gasto_repo_directo))

    # ============================================================
    # update_shared_expense() — editar descripción
    # ============================================================
    print("\n--- update_shared_expense() ---")
    gasto_editable = svc.add_shared_expense(
        hogar_id=hogar, pagador="bruno", origen_tipo="transaccion", origen_id=8006,
        categoria_id=cat_id, monto_base_minor=1200, coeficiente_deuda=50.0, fecha="2026-01-07",
        descripcion="Descripción original",
    ).entity_id

    resultado_update = svc.update_shared_expense(gasto_id=gasto_editable, hogar_id=hogar, descripcion="Descripción corregida")
    caso("update_shared_expense() devuelve success=True", True, resultado_update.success)
    caso(
        "update_shared_expense() reescribe la descripción",
        "Descripción corregida",
        svc._gastos_repo.obtener_por_id(gasto_editable)["descripcion"],
    )

    svc.update_shared_expense(gasto_id=gasto_editable, hogar_id=hogar, descripcion=None)
    caso(
        "update_shared_expense(descripcion=None) limpia la descripción",
        None,
        svc._gastos_repo.obtener_por_id(gasto_editable)["descripcion"],
    )

    caso_excepcion(
        "update_shared_expense() con gasto_id inexistente lanza GastoCompartidoNotFoundError",
        GastoCompartidoNotFoundError,
        lambda: svc.update_shared_expense(gasto_id=999999, hogar_id=hogar, descripcion="X"),
    )
    caso_excepcion(
        "update_shared_expense() con un gasto que pertenece a OTRO hogar lanza GastoCompartidoNotFoundError",
        GastoCompartidoNotFoundError,
        lambda: svc.update_shared_expense(gasto_id=gasto_editable, hogar_id=hogar_2, descripcion="X"),
    )

    manager.desconectar()

    print(f"\n--- Resumen: {casos_ok}/{casos_total} casos OK ---")


if __name__ == "__main__":
    main()
