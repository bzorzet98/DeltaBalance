"""
verify/deudas/verify_duplicado_deudas.py

Verifica el chequeo de duplicados de DeudasRepository.crear() sobre la
tabla en su estructura final (tabs + monto con signo): crear() dos veces seguidas con
la misma entidad_persona/tab/monto_minor/fecha en menos de
VENTANA_DUPLICADO_SEGUNDOS lanza DeudaDuplicadaError la segunda vez (un
doble-click); después de esa ventana (simulada ajustando creada_en a mano,
sin esperar tiempo real) sí permite una fila idéntica — dos movimientos
iguales el mismo día son legítimos. DebtsService lo traduce a DebtError.
Mismo patrón que verify/transacciones/verify_duplicado_transacciones.py.

Correlo con:
    python verify/deudas/verify_duplicado_deudas.py
"""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from verify._dummy_db import crear_dummy_db
from db.database import DatabaseManager
from repositories.deudas_repository import VENTANA_DUPLICADO_SEGUNDOS, DeudaDuplicadaError, DeudasRepository
from services.debts_service import DebtError, DebtsService


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
    repo = DeudasRepository(manager)
    ars = manager.fetchone("SELECT id FROM monedas WHERE codigo = 'ARS';")["id"]
    campos = dict(entidad_persona="NOE", concepto="Cena", tab="me_deben", monto_minor=5000, moneda_id=ars, fecha="2026-03-01")

    def contar() -> int:
        return manager.fetchone("SELECT COUNT(*) AS n FROM deudas WHERE entidad_persona = 'NOE';")["n"]

    primera = repo.crear(**campos)
    caso("la primera se crea", 1, contar())
    caso_excepcion("la segunda idéntica, enseguida → DeudaDuplicadaError", DeudaDuplicadaError, lambda: repo.crear(**campos))
    caso("no se insertó una segunda fila", 1, contar())
    repo.crear(**{**campos, "monto_minor": 5001})
    caso("con otro monto no es duplicado", 2, contar())

    # Simula que pasó la ventana (sin esperar tiempo real).
    manager.execute(
        "UPDATE deudas SET creada_en = datetime('now', ?) WHERE id = ?;",
        (f"-{VENTANA_DUPLICADO_SEGUNDOS + 1} seconds", primera),
    )
    repo.crear(**campos)
    caso("pasada la ventana, una fila idéntica sí se crea (dos movimientos iguales el mismo día)", 3, contar())

    svc = DebtsService(manager)
    caso_excepcion(
        "vía DebtsService el doble-click sube como DebtError", DebtError,
        lambda: svc.create("Noe", "Cena", "me_deben", 5000, ars, "2026-03-01"),
    )

    print(f"\n{casos_ok}/{casos_total} casos OK")


if __name__ == "__main__":
    main()
