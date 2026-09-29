"""
verify/compras_cuotas/verify_delete_purchase.py

Verifica FeesService.delete_purchase() — el borrado físico que usa el
botón "Eliminar" de ui/screens/compras_cuotas.py para compras cargadas por
error (ventana de corrección temprana, CLAUDE.md §4). Si el borrado se
bloquea, la pantalla cancela la compra con cancel_purchase().

Qué se prueba y por qué:
- Una compra con todas sus cuotas 'pendiente' se borra entera: la compra y
  sus cuotas_credito desaparecen en la misma transacción.
- Una compra cancelada (sus cuotas pasaron a 'omitido') también se puede
  borrar: ninguna cuota llegó a un resumen.
- Se bloquea (FeesError) y NO borra nada si:
    * alguna cuota ya está 'en_resumen' (pertenece a un resumen),
    * la compra está compartida (gastos_compartidos por cuota),
    * hay una deuda generada desde la compra (deudas.origen_tipo=
      'compra_cuotas').
- Una compra inexistente lanza PurchaseNotFoundError.

Correlo con:
    python verify/compras_cuotas/verify_delete_purchase.py
"""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from verify._dummy_db import crear_dummy_db
from db.database import DatabaseManager
from repositories.cuentas_repository import CuentasRepository
from services.debts_service import DebtsService
from services.fees_service import FeesError, FeesService, PurchaseNotFoundError
from services.shared_expenses_service import SharedExpensesService


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
    cuentas_repo = CuentasRepository(manager)
    svc = FeesService(manager)
    shared_svc = SharedExpensesService(manager)
    debts_svc = DebtsService(manager)

    cuenta = cuentas_repo.crear(nombre="Tarjeta Delete", tipo="credito", moneda_codigo="ARS")
    categoria = manager.fetchall("SELECT id FROM categorias WHERE tipo = 'egreso' ORDER BY id LIMIT 1;")[0]["id"]

    def nueva_compra(concepto: str) -> int:
        # Conceptos distintos en cada compra: CompraDuplicadaError bloquea
        # compras idénticas creadas con menos de 5 segundos de diferencia.
        return svc.create_purchase(
            date_str="2026-03-05", concept=concepto, account_id=cuenta, category_id=categoria,
            currency_code="ARS", total_amount=3000.0, total_fees=3,
        ).entity_id

    def existe_compra(compra_id: int) -> bool:
        return bool(manager.fetchall("SELECT id FROM compras_cuotas WHERE id = ?;", (compra_id,)))

    def cantidad_cuotas(compra_id: int) -> int:
        return len(manager.fetchall("SELECT id FROM cuotas_credito WHERE compra_id = ?;", (compra_id,)))

    # ============================================================
    print("--- Compra con todas las cuotas 'pendiente': se borra entera ---")
    # ============================================================
    compra_1 = nueva_compra("Cargada por error")
    caso("antes de borrar tiene 3 cuotas", 3, cantidad_cuotas(compra_1))
    res = svc.delete_purchase(compra_1)
    caso("delete_purchase() devuelve success=True", True, res.success)
    caso("informa 3 cuotas borradas", 3, res.data["fees_deleted"])
    caso("la compra ya no existe", False, existe_compra(compra_1))
    caso("sus cuotas tampoco", 0, cantidad_cuotas(compra_1))

    # ============================================================
    print("\n--- Compra cancelada (cuotas 'omitido'): también se puede borrar ---")
    # ============================================================
    compra_2 = nueva_compra("Cancelada por error")
    svc.cancel_purchase(compra_2)
    caso(
        "después de cancelar, sus cuotas quedan 'omitido'",
        ["omitido", "omitido", "omitido"],
        [c["estado"] for c in svc.get_fees_for_purchase(compra_2)],
    )
    svc.delete_purchase(compra_2)
    caso("la compra cancelada se borró", False, existe_compra(compra_2))
    caso("sus cuotas omitidas también", 0, cantidad_cuotas(compra_2))

    # ============================================================
    print("\n--- §4: una cuota ya en un resumen bloquea el borrado ---")
    # ============================================================
    compra_3 = nueva_compra("Con cuota en resumen")
    resumen = svc.open_statement(account_id=cuenta, month=3, year=2026).entity_id
    svc.confirm_fee(fee_id=svc.get_fees_for_purchase(compra_3)[0]["id"], statement_id=resumen)
    caso_excepcion("borrar con una cuota 'en_resumen' → FeesError", FeesError, lambda: svc.delete_purchase(compra_3))
    caso("la compra sigue existiendo", True, existe_compra(compra_3))
    caso("sus 3 cuotas siguen existiendo", 3, cantidad_cuotas(compra_3))

    # ============================================================
    print("\n--- §4: una compra compartida (un gasto por cuota) bloquea el borrado ---")
    # ============================================================
    hogar = shared_svc.create_hogar(nombre_creador_local="bruno", nombre_hogar="Hogar Delete").entity_id
    compra_4 = nueva_compra("Compartida")
    shared_svc.add_shared_purchase(compra_id=compra_4, hogar_id=hogar, pagador="bruno", coeficiente_deuda=50.0)
    caso_excepcion("borrar una compra compartida → FeesError", FeesError, lambda: svc.delete_purchase(compra_4))
    caso("la compra compartida sigue existiendo", True, existe_compra(compra_4))
    caso("sus cuotas siguen existiendo", 3, cantidad_cuotas(compra_4))

    # ============================================================
    print("\n--- §4: una deuda generada desde la compra bloquea el borrado ---")
    # ============================================================
    compra_5 = nueva_compra("Con deuda")
    debts_svc.create(
        person="Ana", debt_type="a_favor", amount=1500.0, currency_code="ARS", date_str="2026-03-05",
        concept="Mitad de la compra", origen_tipo="compra_cuotas", origen_id=compra_5,
    )
    caso_excepcion("borrar una compra con deuda vinculada → FeesError", FeesError, lambda: svc.delete_purchase(compra_5))
    caso("la compra con deuda sigue existiendo", True, existe_compra(compra_5))
    caso("sus cuotas siguen existiendo", 3, cantidad_cuotas(compra_5))

    # ============================================================
    print("\n--- Compra inexistente ---")
    # ============================================================
    caso_excepcion("compra inexistente → PurchaseNotFoundError", PurchaseNotFoundError, lambda: svc.delete_purchase(999999))

    print(f"\n{casos_ok}/{casos_total} casos OK")


if __name__ == "__main__":
    main()
