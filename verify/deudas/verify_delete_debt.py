"""
verify/deudas/verify_delete_debt.py

Verifica DebtsService.delete() / DeudasRepository.eliminar() sobre la
estructura final de deudas (tabs + monto con signo): una fila no tiene
dependencias con estado propio (un pago ya no es un registro colgado de la
deuda, es otra fila negativa en el mismo tab), así que cualquier fila se
borra directo (ventana de corrección temprana, CLAUDE.md §4). Borrar el
pago vuelve a dejar el saldo como antes del pago.

Cubre:
  - delete() de una deuda con un pago (fila negativa) registrado: se borra
    igual, y el pago sigue ahí (son independientes).
  - delete() del pago: el saldo vuelve a lo de antes.
  - delete() de un id inexistente: DebtNotFoundError.
  - DeudasRepository.eliminar(): True si borró, False si no había nada.

Correlo con:
    python verify/deudas/verify_delete_debt.py
"""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from verify._dummy_db import crear_dummy_db
from db.database import DatabaseManager
from repositories.deudas_repository import DeudasRepository
from services.debts_service import DebtNotFoundError, DebtsService


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
    svc = DebtsService(manager)
    repo = DeudasRepository(manager)
    ars = manager.fetchone("SELECT id FROM monedas WHERE codigo = 'ARS';")["id"]

    def saldo_noe() -> int:
        return next(
            (e["saldo_minor"] for e in svc.summary_by_person("me_deben", "2026-12-31")
             if e["entidad_persona"] == "NOE" and e["moneda_id"] == ars),
            0,
        )

    prestamo = svc.create("Noe", "Préstamo", "me_deben", 40000, ars, "2026-03-01").entity_id
    pago = svc.create("Noe", "Devolvió una parte", "me_deben", -10000, ars, "2026-03-20").entity_id
    otra = svc.create("Noe", "Otra", "me_deben", 2000, ars, "2026-04-01").entity_id
    caso("saldo inicial de NOE: 400 − 100 + 20", 32000, saldo_noe())

    print("--- delete() ---")
    caso("borrar el pago → success=True", True, svc.delete(pago).success)
    caso("sin el pago, el saldo vuelve a 400 + 20", 42000, saldo_noe())
    caso("borrar el préstamo (tenía un pago registrado antes): se puede", True, svc.delete(prestamo).success)
    caso("queda solo la otra fila", [otra], [f["id"] for f in svc.list_by_tab("me_deben")])
    caso_excepcion("delete() de un id inexistente → DebtNotFoundError", DebtNotFoundError, lambda: svc.delete(prestamo))

    print("\n--- DeudasRepository.eliminar() ---")
    caso("eliminar() de una fila existente → True", True, repo.eliminar(otra))
    caso("eliminar() de la misma otra vez → False", False, repo.eliminar(otra))

    print(f"\n{casos_ok}/{casos_total} casos OK")


if __name__ == "__main__":
    main()
