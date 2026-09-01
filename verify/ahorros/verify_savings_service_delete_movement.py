"""
verify/ahorros/verify_savings_service_delete_movement.py

Verifica SavingsService.delete_movement() (services/savings_service.py,
Parte B de la tarea de "borrado por fila" — Ahorros): borra el
movimiento_activo y sus asignaciones, atómico; con
eliminar_transaccion_vinculada=False (default) NO toca la transacción real
vinculada aunque exista; con eliminar_transaccion_vinculada=True SÍ la
soft-elimina (mismo mecanismo que TransactionService.delete()); sobre un
movimiento sin transaccion_id, el flag no tiene efecto; MovimientoNotFoundError
si el id no existe; atomicidad real con rollback simulado (si el borrado de
la transacción vinculada falla a mitad de camino, ni las asignaciones ni el
movimiento quedan borrados).

Correlo con:
    python verify/ahorros/verify_savings_service_delete_movement.py
"""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from verify._dummy_db import crear_dummy_db
from db.database import DatabaseManager
from repositories.cuentas_repository import CuentasRepository
from repositories.transacciones_repository import TransaccionesRepository
from services.savings_service import SavingsService, MovimientoNotFoundError


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
    cuentas_repo = CuentasRepository(manager)

    moneda_ars = manager.fetchone("SELECT id FROM monedas WHERE codigo = 'ARS';")["id"]
    categoria_inversiones = manager.fetchone(
        "SELECT id FROM categorias WHERE categoria_principal = 'MOVIMIENTO CAPITAL' "
        "AND subcategoria = 'Inversiones';"
    )["id"]
    cuenta_mp = cuentas_repo.crear(
        nombre="Mercado Pago (verify delete_movement)", tipo="debito", moneda_codigo="ARS", saldo_inicial=1000.0,
    )
    activo = svc.create_activo(nombre="FCI delete_movement", tipo="fci", moneda_id=moneda_ars).entity_id
    objetivo = svc.create_objetivo(nombre="Objetivo delete_movement").entity_id

    print("--- delete_movement() sobre un movimiento SIN transaccion_id vinculado ---")
    compra_informal = svc.register_purchase(
        activo_id=activo, fecha="2026-01-05", monto_total_minor=10000,
        asignaciones=[{"objetivo_id": objetivo, "porcentaje": 100.0}],
    )
    mov_informal_id = compra_informal.entity_id
    resultado_informal = svc.delete_movement(mov_informal_id)
    caso("delete_movement() sin vínculo: success=True", True, resultado_informal.success)
    caso("delete_movement() sin vínculo: data['transaccion_id_eliminada'] = None", None, resultado_informal.data["transaccion_id_eliminada"])
    caso("delete_movement() borra el movimiento de verdad", None, manager.fetchone(
        "SELECT id FROM movimientos_activo WHERE id = ?;", (mov_informal_id,)
    ))
    caso("delete_movement() borra también sus asignaciones", 0, manager.fetchone(
        "SELECT COUNT(*) AS n FROM asignaciones WHERE movimiento_id = ?;", (mov_informal_id,)
    )["n"])

    print("\n--- delete_movement() con transaccion_id vinculado, eliminar_transaccion_vinculada=False (default) ---")
    compra_vinculada = svc.register_purchase(
        activo_id=activo, fecha="2026-01-06", monto_total_minor=20000,
        asignaciones=[{"objetivo_id": objetivo, "porcentaje": 100.0}],
        cuenta_id=cuenta_mp, categoria_id=categoria_inversiones,
    )
    mov_vinculado_id = compra_vinculada.entity_id
    tx_vinculada_id = compra_vinculada.data["transaccion_id"]
    caso("la compra vinculada sí generó una transaccion_id", True, isinstance(tx_vinculada_id, int))

    resultado_sin_borrar_tx = svc.delete_movement(mov_vinculado_id)
    caso("delete_movement() default: success=True", True, resultado_sin_borrar_tx.success)
    caso(
        "delete_movement() default: data['transaccion_id_eliminada'] = None (no se tocó)",
        None, resultado_sin_borrar_tx.data["transaccion_id_eliminada"],
    )
    caso("el movimiento vinculado sí se borró", None, manager.fetchone(
        "SELECT id FROM movimientos_activo WHERE id = ?;", (mov_vinculado_id,)
    ))
    fila_tx_intacta = manager.fetchone("SELECT deleted_at FROM transacciones WHERE id = ?;", (tx_vinculada_id,))
    caso("la transacción vinculada NO se tocó (deleted_at sigue NULL)", None, fila_tx_intacta["deleted_at"] if fila_tx_intacta else "no encontrada")

    print("\n--- delete_movement() con transaccion_id vinculado, eliminar_transaccion_vinculada=True ---")
    compra_vinculada_2 = svc.register_purchase(
        activo_id=activo, fecha="2026-01-07", monto_total_minor=30000,
        asignaciones=[{"objetivo_id": objetivo, "porcentaje": 100.0}],
        cuenta_id=cuenta_mp, categoria_id=categoria_inversiones,
    )
    mov_vinculado_2_id = compra_vinculada_2.entity_id
    tx_vinculada_2_id = compra_vinculada_2.data["transaccion_id"]

    resultado_con_borrar_tx = svc.delete_movement(mov_vinculado_2_id, eliminar_transaccion_vinculada=True)
    caso("delete_movement(True): success=True", True, resultado_con_borrar_tx.success)
    caso(
        "delete_movement(True): data['transaccion_id_eliminada'] = la transacción real",
        tx_vinculada_2_id, resultado_con_borrar_tx.data["transaccion_id_eliminada"],
    )
    fila_tx_borrada = manager.fetchone("SELECT deleted_at FROM transacciones WHERE id = ?;", (tx_vinculada_2_id,))
    caso("la transacción vinculada quedó soft-eliminada (deleted_at seteado)", True, fila_tx_borrada["deleted_at"] is not None if fila_tx_borrada else False)
    caso("la transacción sigue existiendo físicamente (soft-delete, no DELETE físico)", True, fila_tx_borrada is not None)

    print("\n--- delete_movement() sobre un id inexistente ---")
    caso_excepcion(
        "delete_movement() con movimiento_id inexistente lanza MovimientoNotFoundError",
        MovimientoNotFoundError,
        lambda: svc.delete_movement(999999),
    )

    print("\n--- delete_movement() — atomicidad real: rollback si el borrado de la transacción falla a mitad de camino ---")
    compra_rollback = svc.register_purchase(
        activo_id=activo, fecha="2026-01-08", monto_total_minor=40000,
        asignaciones=[{"objetivo_id": objetivo, "porcentaje": 100.0}],
        cuenta_id=cuenta_mp, categoria_id=categoria_inversiones,
    )
    mov_rollback_id = compra_rollback.entity_id
    tx_rollback_id = compra_rollback.data["transaccion_id"]

    metodo_original = TransaccionesRepository.eliminar

    def eliminar_que_falla(self, *args, **kwargs):
        raise RuntimeError("Fallo simulado en TransaccionesRepository.eliminar()")

    TransaccionesRepository.eliminar = eliminar_que_falla
    try:
        caso_excepcion(
            "delete_movement(True) propaga la excepción si el borrado de la transacción falla",
            RuntimeError,
            lambda: svc.delete_movement(mov_rollback_id, eliminar_transaccion_vinculada=True),
        )
    finally:
        TransaccionesRepository.eliminar = metodo_original

    caso("rollback: el movimiento NO quedó borrado", "compra", manager.fetchone(
        "SELECT tipo FROM movimientos_activo WHERE id = ?;", (mov_rollback_id,)
    )["tipo"] if manager.fetchone("SELECT tipo FROM movimientos_activo WHERE id = ?;", (mov_rollback_id,)) else None)
    caso("rollback: las asignaciones tampoco quedaron borradas", 1, manager.fetchone(
        "SELECT COUNT(*) AS n FROM asignaciones WHERE movimiento_id = ?;", (mov_rollback_id,)
    )["n"])
    fila_tx_rollback = manager.fetchone("SELECT deleted_at FROM transacciones WHERE id = ?;", (tx_rollback_id,))
    caso("rollback: la transacción vinculada tampoco quedó soft-eliminada", None, fila_tx_rollback["deleted_at"] if fila_tx_rollback else "no encontrada")

    manager.desconectar()

    print(f"\n--- Resumen: {casos_ok}/{casos_total} casos OK ---")


if __name__ == "__main__":
    main()
