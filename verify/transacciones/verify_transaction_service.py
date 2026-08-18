"""
verify/transacciones/verify_transaction_service.py

Verifica TransactionService COMPLETO (no el repositorio pelado) después de
la migración parcial a TransaccionesRepository (Fase 2, bloque
TRANSACCIONES paso 2): que create()/list_transactions()/update()/delete()
siguen funcionando end-to-end con las validaciones de negocio intactas.

Este es el chequeo que más importa de esta migración: confirmar que mover
delete() a usar el repositorio (y dejar get()/list_transactions()/create()/
update() sin migrar, ver comentarios en transaction_service.py) NO cambió
ningún comportamiento observable — ni las excepciones de negocio
(AccountNotFoundError, CategoryNotFoundError, CurrencyNotFoundError,
TransactionError, ValueError), ni el shape de las filas enriquecidas que
devuelven get()/list_transactions(), ni el "tag/notes='' borra el campo" de
update() (que fue justamente la razón para NO migrar update()).

Correlo con:
    python verify/transacciones/verify_transaction_service.py
"""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from verify._dummy_db import crear_dummy_db
from db.database import DatabaseManager
from repositories.cuentas_repository import CuentasRepository
from repositories.transacciones_repository import TransaccionesRepository
from services.transaction_service import (
    TransactionService,
    TransactionError,
    AccountNotFoundError,
    CategoryNotFoundError,
    CurrencyNotFoundError,
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
    svc = TransactionService(manager)

    cuenta_id = manager.fetchone("SELECT id FROM cuentas WHERE activa = 1 LIMIT 1;")["id"]
    categoria_egreso_id = manager.fetchone("SELECT id FROM categorias WHERE tipo = 'egreso' LIMIT 1;")["id"]

    print("--- create() — camino feliz ---")
    resultado = svc.create(
        date_str="2026-03-01",
        concept="Test Service Create",
        account_id=cuenta_id,
        category_id=categoria_egreso_id,
        currency_code="ARS",
        amount=250.0,
        movement_type="egreso",
        notes="nota original",
    )
    caso("create() devuelve success=True", True, resultado.success)
    caso("create() devuelve un transaction_id numérico", True, isinstance(resultado.transaction_id, int))
    tx_id = resultado.transaction_id

    print("\n--- create() — validaciones de negocio intactas ---")
    caso_excepcion(
        "create() con account_id inexistente sigue lanzando AccountNotFoundError",
        AccountNotFoundError,
        lambda: svc.create(
            date_str="2026-03-01", concept="X", account_id=999999,
            category_id=categoria_egreso_id, currency_code="ARS",
            amount=10.0, movement_type="egreso",
        ),
    )
    caso_excepcion(
        "create() con category_id inexistente sigue lanzando CategoryNotFoundError",
        CategoryNotFoundError,
        lambda: svc.create(
            date_str="2026-03-01", concept="X", account_id=cuenta_id,
            category_id=999999, currency_code="ARS",
            amount=10.0, movement_type="egreso",
        ),
    )
    caso_excepcion(
        "create() con currency_code inexistente sigue lanzando CurrencyNotFoundError",
        CurrencyNotFoundError,
        lambda: svc.create(
            date_str="2026-03-01", concept="X", account_id=cuenta_id,
            category_id=categoria_egreso_id, currency_code="XYZ",
            amount=10.0, movement_type="egreso",
        ),
    )
    caso_excepcion(
        "create() con monto <= 0 sigue lanzando ValueError",
        ValueError,
        lambda: svc.create(
            date_str="2026-03-01", concept="X", account_id=cuenta_id,
            category_id=categoria_egreso_id, currency_code="ARS",
            amount=0.0, movement_type="egreso",
        ),
    )
    caso_excepcion(
        "create() con concept vacío sigue lanzando TransactionError",
        TransactionError,
        lambda: svc.create(
            date_str="2026-03-01", concept="   ", account_id=cuenta_id,
            category_id=categoria_egreso_id, currency_code="ARS",
            amount=10.0, movement_type="egreso",
        ),
    )

    print("\n--- get() — sigue enriquecido con JOINs (no migrado, a propósito) ---")
    fila = svc.get(tx_id)
    caso("get() encuentra la transacción", "Test Service Create", fila["concepto"] if fila else None)
    caso("get() sigue trayendo account_name (JOIN con cuentas)", True, "account_name" in fila.keys() if fila else False)
    caso("get() sigue trayendo currency_code (JOIN con monedas)", "ARS", fila["currency_code"] if fila else None)

    print("\n--- list_transactions() — filtros + shape enriquecido intactos ---")
    listado = svc.list_transactions(account_id=cuenta_id)
    ids_listado = [r["id"] for r in listado]
    caso("list_transactions(account_id=...) incluye la transacción creada", True, tx_id in ids_listado)
    fila_lista = next((r for r in listado if r["id"] == tx_id), None)
    caso("list_transactions() sigue trayendo account_name enriquecido", True, "account_name" in fila_lista.keys() if fila_lista else False)

    print("\n--- update() — sigue soportando notes='' para borrar el campo ---")
    res_update_normal = svc.update(tx_id, concept="Concepto actualizado")
    caso("update() de un campo simple devuelve success=True", True, res_update_normal.success)
    caso("update() persiste el nuevo concepto", "Concepto actualizado", svc.get(tx_id)["concepto"])

    res_update_clear = svc.update(tx_id, notes="")
    caso("update(notes='') devuelve success=True", True, res_update_clear.success)
    caso("update(notes='') borra notas (queda NULL, no '')", None, svc.get(tx_id)["notas"])

    print("\n--- delete() — migrado al repositorio, mismo comportamiento observable ---")
    res_delete = svc.delete(tx_id)
    caso("delete() devuelve success=True", True, res_delete.success)
    caso("delete() devuelve el mismo transaction_id", tx_id, res_delete.transaction_id)

    listado_tras_delete = svc.list_transactions(account_id=cuenta_id)
    caso(
        "list_transactions() ya no incluye la transacción eliminada",
        False,
        tx_id in [r["id"] for r in listado_tras_delete],
    )
    caso("get() sigue encontrando la transacción eliminada (include_deleted=True, sin cambios)", True, svc.get(tx_id) is not None)

    res_delete_otra_vez = svc.delete(tx_id)
    caso("delete() sobre algo ya eliminado sigue siendo idempotente (success=True)", True, res_delete_otra_vez.success)

    res_delete_inexistente = svc.delete(999999)
    caso("delete() de un id inexistente devuelve success=False", False, res_delete_inexistente.success)

    # ============================================================
    # create_transfer() — vínculo real en autotransferencias (hueco cerrado)
    # ============================================================
    print("\n--- create_transfer() — deja un vínculo real en autotransferencias ---")
    cuentas_repo = CuentasRepository(manager)
    cuenta_destino_id = cuentas_repo.crear(nombre="Cuenta Destino", tipo="debito")

    resultado_transfer = svc.create_transfer(
        date_str="2026-04-01",
        origin_account_id=cuenta_id,
        dest_account_id=cuenta_destino_id,
        currency_code="ARS",
        amount=500.0,
        category_id=categoria_egreso_id,
        notes="Transferencia de prueba",
    )
    caso("create_transfer() devuelve success=True", True, resultado_transfer.success)
    out_id = resultado_transfer.data["out_transaction_id"]
    in_id = resultado_transfer.data["in_transaction_id"]
    caso("create_transfer() devuelve dos transaction_id distintos", True, out_id != in_id)

    vinculo = manager.fetchone(
        "SELECT * FROM autotransferencias WHERE transaccion_salida_id = ? AND transaccion_entrada_id = ?;",
        (out_id, in_id),
    )
    caso("create_transfer() deja una fila real en autotransferencias", True, vinculo is not None)
    caso(
        "el vínculo apunta a transaccion_salida_id = out_id (el id concreto, no cualquier fila)",
        out_id,
        vinculo["transaccion_salida_id"] if vinculo else None,
    )
    caso(
        "el vínculo apunta a transaccion_entrada_id = in_id (el id concreto, no cualquier fila)",
        in_id,
        vinculo["transaccion_entrada_id"] if vinculo else None,
    )
    caso("el vínculo persiste las notas de la transferencia", "Transferencia de prueba", vinculo["notas"] if vinculo else None)

    total_vinculos = manager.fetchone("SELECT COUNT(*) AS n FROM autotransferencias;")["n"]
    caso("create_transfer() crea exactamente UN vínculo (no uno de más ni de menos)", 1, total_vinculos)

    # ============================================================
    # create_transfer() — atomicidad real: rollback si el vínculo falla
    # ============================================================
    print("\n--- create_transfer() — rollback si crear_autotransferencia() falla a mitad de camino ---")
    transacciones_antes = manager.fetchone("SELECT COUNT(*) AS n FROM transacciones;")["n"]
    vinculos_antes = manager.fetchone("SELECT COUNT(*) AS n FROM autotransferencias;")["n"]

    metodo_original = TransaccionesRepository.crear_autotransferencia

    def crear_autotransferencia_que_falla(self, *args, **kwargs):
        raise RuntimeError("Fallo simulado en crear_autotransferencia()")

    TransaccionesRepository.crear_autotransferencia = crear_autotransferencia_que_falla
    try:
        caso_excepcion(
            "create_transfer() propaga la excepción si crear_autotransferencia() falla a mitad de camino",
            RuntimeError,
            lambda: svc.create_transfer(
                date_str="2026-04-02",
                origin_account_id=cuenta_id,
                dest_account_id=cuenta_destino_id,
                currency_code="ARS",
                amount=300.0,
                category_id=categoria_egreso_id,
            ),
        )
    finally:
        TransaccionesRepository.crear_autotransferencia = metodo_original

    transacciones_despues = manager.fetchone("SELECT COUNT(*) AS n FROM transacciones;")["n"]
    vinculos_despues = manager.fetchone("SELECT COUNT(*) AS n FROM autotransferencias;")["n"]
    caso(
        "rollback: las dos transacciones del intento fallido NO quedan persistidas (mismo conteo que antes)",
        transacciones_antes,
        transacciones_despues,
    )
    caso(
        "rollback: tampoco queda ningún vínculo huérfano en autotransferencias",
        vinculos_antes,
        vinculos_despues,
    )

    manager.desconectar()

    print(f"\n--- Resumen: {casos_ok}/{casos_total} casos OK ---")


if __name__ == "__main__":
    main()
