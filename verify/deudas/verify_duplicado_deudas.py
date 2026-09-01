"""
verify/deudas/verify_duplicado_deudas.py

Verifica el chequeo de duplicados de DeudasRepository.crear()
(repositories/deudas_repository.py, Tarea 9 Parte B —
docs/PROXIMOS_PASOS.md): crear() dos veces seguidas con el mismo
entidad_persona/tipo/monto_original_minor/fecha_inicio en menos de
VENTANA_DUPLICADO_SEGUNDOS lanza DeudaDuplicadaError la segunda vez;
después de esa ventana (simulada ajustando creada_en a mano, no esperando
tiempo real) sí permite crear una fila idéntica. Mismo patrón exacto que
verify/transacciones/verify_duplicado_transacciones.py.

Correlo con:
    python verify/deudas/verify_duplicado_deudas.py
"""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from verify._dummy_db import crear_dummy_db
from db.database import DatabaseManager
from repositories.deudas_repository import (
    DeudasRepository,
    DeudaDuplicadaError,
    VENTANA_DUPLICADO_SEGUNDOS,
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
    repo = DeudasRepository(manager)

    moneda_ars = manager.fetchone("SELECT id FROM monedas WHERE codigo = 'ARS';")["id"]

    campos_comunes = dict(
        entidad_persona="Noe Dup",
        tipo="a_favor",
        monto_minor=500000,
        moneda_id=moneda_ars,
        fecha_inicio="2026-01-10",
    )

    print("--- crear() — primera vez, sin duplicado previo ---")
    deuda_1 = repo.crear(**campos_comunes)
    caso("primera creación devuelve un id numérico", True, isinstance(deuda_1, int))

    print("\n--- crear() — segunda vez idéntica, dentro de la ventana de 5s ---")
    caso_excepcion(
        f"crear() idéntico a menos de {VENTANA_DUPLICADO_SEGUNDOS}s lanza DeudaDuplicadaError",
        DeudaDuplicadaError,
        lambda: repo.crear(**campos_comunes),
    )
    total_filas_tras_intento = manager.fetchone(
        "SELECT COUNT(*) AS n FROM deudas WHERE entidad_persona = ?;", (campos_comunes["entidad_persona"],)
    )["n"]
    caso("el intento duplicado rechazado NO insertó una segunda fila", 1, total_filas_tras_intento)

    print("\n--- crear() — con un campo distinto (monto) no cuenta como duplicado ---")
    campos_monto_distinto = dict(campos_comunes, monto_minor=999999)
    deuda_2 = repo.crear(**campos_monto_distinto)
    caso("crear() con monto distinto no dispara el chequeo de duplicado", True, isinstance(deuda_2, int))

    print("\n--- crear() — con tipo distinto (mismo resto) no cuenta como duplicado ---")
    campos_tipo_distinto = dict(campos_comunes, tipo="en_contra")
    deuda_3 = repo.crear(**campos_tipo_distinto)
    caso("crear() con tipo distinto no dispara el chequeo de duplicado", True, isinstance(deuda_3, int))

    print(f"\n--- crear() — tras simular que pasaron {VENTANA_DUPLICADO_SEGUNDOS + 1}s (ajustando creada_en a mano) ---")
    manager.execute(
        "UPDATE deudas SET creada_en = datetime('now', ?) WHERE id = ?;",
        (f"-{VENTANA_DUPLICADO_SEGUNDOS + 1} seconds", deuda_1),
    )
    deuda_4 = repo.crear(**campos_comunes)
    caso("tras la ventana de duplicado, crear() idéntico sí se permite", True, isinstance(deuda_4, int))
    total_filas_final = manager.fetchone(
        "SELECT COUNT(*) AS n FROM deudas WHERE entidad_persona = ? AND monto_original_minor = ?;",
        (campos_comunes["entidad_persona"], campos_comunes["monto_minor"]),
    )["n"]
    caso("ahora sí existen dos filas idénticas (deuda_1 y deuda_4)", 2, total_filas_final)

    manager.desconectar()

    print(f"\n--- Resumen: {casos_ok}/{casos_total} casos OK ---")


if __name__ == "__main__":
    main()
