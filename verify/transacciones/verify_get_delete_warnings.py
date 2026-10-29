"""
verify/transacciones/verify_get_delete_warnings.py

Verifica TransactionService.get_delete_warnings() (Parte B de la tarea de
"borrado por fila" — Registro de transacciones): detecta correctamente
cuándo una transacción es una pata de una autotransferencia (desde el
schema v4 las dos patas son independientes y se reconocen por su tag
"autotransferencia" — docs/DATA_MODEL_DECISIONS.md sección 40) o es el
origen de un movimiento de ahorro (movimientos_activo.transaccion_id), y
que NO reporta nada para una transacción suelta. También confirma
que TransactionService.delete() (ya cubierto en
verify_transaction_service.py) sigue funcionando igual con o sin vínculos —
get_delete_warnings() solo informa, nunca bloquea el borrado.

Correlo con:
    python verify/transacciones/verify_get_delete_warnings.py
"""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from verify._dummy_db import crear_dummy_db
from db.database import DatabaseManager
from repositories.cuentas_repository import CuentasRepository
from services.transacciones_service import TransactionService
from services.ahorros_service import SavingsService


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

    db_path = crear_dummy_db()
    print(f"Dummy DB creada en: {db_path}\n")

    manager = DatabaseManager(db_path=db_path)
    manager.inicializar()
    tx_svc = TransactionService(manager)
    savings_svc = SavingsService(manager)
    cuentas_repo = CuentasRepository(manager)

    cuenta_a = cuentas_repo.crear(nombre="Cuenta A Warnings", tipo="efectivo", moneda_codigo="ARS")
    cuenta_b = cuentas_repo.crear(nombre="Cuenta B Warnings", tipo="efectivo", moneda_codigo="ARS")
    cuenta_mp = cuentas_repo.crear(nombre="MP Warnings", tipo="debito", moneda_codigo="ARS", saldo_inicial=1000.0)
    cat_egreso = manager.fetchone("SELECT id FROM categorias WHERE tipo = 'egreso' LIMIT 1;")["id"]
    cat_transferencia = manager.fetchone(
        "SELECT id FROM categorias WHERE categoria_principal = 'MOVIMIENTO CAPITAL' "
        "AND subcategoria = 'AUTOTRANSFERENCIA';"
    )["id"]
    moneda_ars = "ARS"
    categoria_inversiones = manager.fetchone(
        "SELECT id FROM categorias WHERE categoria_principal = 'MOVIMIENTO CAPITAL' "
        "AND subcategoria = 'INVERSIONES';"
    )["id"]

    print("--- get_delete_warnings() sobre una transacción suelta (sin vínculos) ---")
    tx_suelta = tx_svc.create(
        date_str="2026-01-10", concept="Gasto suelto", account_id=cuenta_a,
        category_id=cat_egreso, currency_code="ARS", amount=1000.0, movement_type="egreso",
    ).transaction_id
    avisos_suelta = tx_svc.get_delete_warnings(tx_suelta)
    caso("transacción suelta: es_autotransferencia=False", False, avisos_suelta["es_autotransferencia"])
    caso("transacción suelta: es_origen_ahorro=False", False, avisos_suelta["es_origen_ahorro"])
    caso("sin vínculo entre patas: el aviso ya no trae transaccion_par_id", False, "transaccion_par_id" in avisos_suelta)

    print("\n--- get_delete_warnings() sobre las dos patas de una autotransferencia ---")
    resultado_transfer = tx_svc.create_transfer(
        date_str="2026-01-11", origin_account_id=cuenta_a, dest_account_id=cuenta_b,
        currency_code="ARS", amount=500.0, category_id=cat_transferencia,
    )
    tx_salida = resultado_transfer.data["out_transaction_id"]
    tx_entrada = resultado_transfer.data["in_transaction_id"]

    avisos_salida = tx_svc.get_delete_warnings(tx_salida)
    caso("pata de salida: es_autotransferencia=True (por su tag)", True, avisos_salida["es_autotransferencia"])
    caso("pata de salida: es_origen_ahorro=False", False, avisos_salida["es_origen_ahorro"])

    avisos_entrada = tx_svc.get_delete_warnings(tx_entrada)
    caso("pata de entrada: es_autotransferencia=True (por su tag)", True, avisos_entrada["es_autotransferencia"])

    print("\n--- borrar una pata no toca la otra (son independientes) ---")
    tx_svc.delete(tx_salida)
    caso("la pata de entrada sigue viva después de borrar la de salida", None,
         manager.fetchone("SELECT deleted_at FROM transacciones WHERE id = ?;", (tx_entrada,))["deleted_at"])
    caso("… y sigue reconocida como pata de una autotransferencia", True,
         tx_svc.get_delete_warnings(tx_entrada)["es_autotransferencia"])

    print("\n--- get_delete_warnings() sobre el origen de un movimiento de ahorro ---")
    activo = savings_svc.create_activo(nombre="FCI Warnings", tipo="fci", moneda_codigo=moneda_ars).entity_id
    objetivo = savings_svc.create_objetivo(nombre="Objetivo Warnings").entity_id
    compra = savings_svc.register_purchase(
        activo_id=activo, fecha="2026-01-12", monto_total_minor=20000,
        asignaciones=[{"objetivo_id": objetivo, "porcentaje": 100.0}],
        cuenta_id=cuenta_mp, categoria_id=categoria_inversiones,
    )
    tx_ahorro = compra.data["transaccion_id"]
    avisos_ahorro = tx_svc.get_delete_warnings(tx_ahorro)
    caso("origen de ahorro: es_origen_ahorro=True", True, avisos_ahorro["es_origen_ahorro"])
    caso("origen de ahorro: es_autotransferencia=False", False, avisos_ahorro["es_autotransferencia"])

    print("\n--- delete() sigue funcionando igual con o sin vínculos (get_delete_warnings() no bloquea nada) ---")
    resultado_delete_suelta = tx_svc.delete(tx_suelta)
    caso("delete() de una transacción sin vínculos: success=True", True, resultado_delete_suelta.success)
    resultado_delete_vinculada = tx_svc.delete(tx_ahorro)
    caso("delete() de una transacción CON vínculo de ahorro: success=True igual (no bloquea)", True, resultado_delete_vinculada.success)
    fila_ahorro_tras_delete = manager.fetchone("SELECT deleted_at FROM transacciones WHERE id = ?;", (tx_ahorro,))
    caso("delete() sobre la transacción vinculada la soft-elimina igual", True, fila_ahorro_tras_delete["deleted_at"] is not None)

    manager.desconectar()

    print(f"\n--- Resumen: {casos_ok}/{casos_total} casos OK ---")


if __name__ == "__main__":
    main()
