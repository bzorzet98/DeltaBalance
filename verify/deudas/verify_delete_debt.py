"""
verify/deudas/verify_delete_debt.py

Verifica DebtsService.delete_debt() / DeudasRepository.eliminar() (Tarea 9,
Parte B — docs/PROXIMOS_PASOS.md): ventana de corrección temprana
(CLAUDE.md §4) aplicada a deudas, mismo criterio ya usado en
AccountsService.delete_account() — borrado real solo si la deuda no tiene
ninguna dependencia con estado propio generado (pagos en deuda_pagos).

Cubre:
  - delete_debt() sin ningún pago registrado: DELETE físico permitido,
    la fila desaparece de verdad (no soft-delete).
  - delete_debt() con al menos un pago registrado (vía register_payment()):
    bloqueado con DebtError, la deuda sigue existiendo intacta.
  - delete_debt() sobre una deuda inexistente: DebtNotFoundError.
  - delete_debt() no exige estado 'activa' — una deuda 'incobrable' (vía
    write_off()) sin ningún pago real también se puede borrar.
  - DeudasRepository.eliminar() en sí (DELETE físico directo, sin la
    validación de negocio — esa vive en el service).

Correlo con:
    python verify/deudas/verify_delete_debt.py
"""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from verify._dummy_db import crear_dummy_db
from db.database import DatabaseManager
from repositories.deudas_repository import DeudasRepository
from services.debts_service import DebtsService, DebtError, DebtNotFoundError


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

    # ============================================================
    # delete_debt() — sin pagos: permitido, DELETE físico real
    # ============================================================
    print("--- delete_debt() — sin pagos registrados ---")
    deuda_sin_pagos = svc.create(
        person="Sin Pagos", debt_type="a_favor", amount=1000.0, currency_code="ARS",
        date_str="2026-01-01", concept="Deuda sin pagos",
    ).debt_id

    resultado_delete = svc.delete_debt(deuda_sin_pagos)
    caso("delete_debt() sin pagos: success=True", True, resultado_delete.success)
    caso("delete_debt() elimina la fila de verdad (obtener_por_id devuelve None)", None, repo.obtener_por_id(deuda_sin_pagos))

    # ============================================================
    # delete_debt() — con pagos: bloqueado
    # ============================================================
    print("\n--- delete_debt() — con al menos un pago registrado ---")
    deuda_con_pago = svc.create(
        person="Con Pago", debt_type="a_favor", amount=5000.0, currency_code="ARS",
        date_str="2026-01-05", concept="Deuda con pago parcial",
    ).debt_id
    svc.register_payment(debt_id=deuda_con_pago, amount=1000.0, currency_code="ARS", date_str="2026-01-10")

    caso_excepcion(
        "delete_debt() con un pago registrado lanza DebtError",
        DebtError,
        lambda: svc.delete_debt(deuda_con_pago),
    )
    caso("la deuda con pago SIGUE existiendo tras el intento de borrado", True, repo.obtener_por_id(deuda_con_pago) is not None)

    # ============================================================
    # delete_debt() — deuda inexistente
    # ============================================================
    print("\n--- delete_debt() — deuda inexistente ---")
    caso_excepcion(
        "delete_debt() con debt_id inexistente lanza DebtNotFoundError",
        DebtNotFoundError,
        lambda: svc.delete_debt(999999),
    )

    # ============================================================
    # delete_debt() — no exige estado 'activa'
    # ============================================================
    print("\n--- delete_debt() — deuda 'incobrable' (write_off) sin pagos también se puede borrar ---")
    deuda_incobrable = svc.create(
        person="Incobrable Sin Pagos", debt_type="a_favor", amount=2000.0, currency_code="ARS",
        date_str="2026-01-15", concept="Se perdona sin haber cobrado nada",
    ).debt_id
    svc.write_off(deuda_incobrable, notes="Nunca va a pagar")

    resultado_delete_incobrable = svc.delete_debt(deuda_incobrable)
    caso("delete_debt() sobre una deuda 'incobrable' sin pagos: success=True", True, resultado_delete_incobrable.success)
    caso("delete_debt() elimina la deuda incobrable de verdad", None, repo.obtener_por_id(deuda_incobrable))

    # ============================================================
    # DeudasRepository.eliminar() directo (sin la validación del service)
    # ============================================================
    print("\n--- DeudasRepository.eliminar() — DELETE físico directo, sin validación de negocio ---")
    deuda_repo_directa = repo.crear(
        entidad_persona="Repo Directo", tipo="a_favor", monto_minor=3000,
        moneda_id=manager.fetchone("SELECT id FROM monedas WHERE codigo = 'ARS';")["id"],
        fecha_inicio="2026-01-20",
    )
    repo.eliminar(deuda_repo_directa)
    caso("repo.eliminar() borra la fila directamente", None, repo.obtener_por_id(deuda_repo_directa))

    manager.desconectar()

    print(f"\n--- Resumen: {casos_ok}/{casos_total} casos OK ---")


if __name__ == "__main__":
    main()
