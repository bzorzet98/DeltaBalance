"""
verify/ahorros/verify_duplicado_movimientos_activo.py

Verifica el chequeo de duplicados de MovimientosActivoRepository.crear()
(repositories/movimientos_activo_repository.py, Parte A de la tarea de
seguridad "bloqueo de duplicados"): crear() dos veces seguidas con los
mismos activo_id/tipo/fecha/monto_total_minor en menos de
VENTANA_DUPLICADO_SEGUNDOS lanza MovimientoDuplicadoError la segunda vez;
después de esa ventana (simulada ajustando creada_en a mano) sí permite
crear un movimiento idéntico.

Correlo con:
    python verify/ahorros/verify_duplicado_movimientos_activo.py
"""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from verify._dummy_db import crear_dummy_db
from db.database import DatabaseManager
from repositories.activos_financieros_repository import ActivosFinancierosRepository
from repositories.movimientos_activo_repository import (
    MovimientosActivoRepository,
    MovimientoDuplicadoError,
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
    activos_repo = ActivosFinancierosRepository(manager)
    repo = MovimientosActivoRepository(manager)

    moneda_usd = manager.fetchone("SELECT id FROM monedas WHERE codigo = 'USD';")["id"]
    activo_1 = activos_repo.crear(nombre="SPY Dup", tipo="accion", moneda_id=moneda_usd)

    campos_comunes = dict(
        activo_id=activo_1,
        tipo="compra",
        fecha="2026-01-05",
        monto_total_minor=100000000,
        cantidad=10.5,
        precio_unitario_minor=9523810,
    )

    print("--- crear() — primera vez, sin duplicado previo ---")
    mov_1 = repo.crear(**campos_comunes)
    caso("primera creación devuelve un id numérico", True, isinstance(mov_1, int))

    print("\n--- crear() — segunda vez idéntica, dentro de la ventana de 5s ---")
    caso_excepcion(
        f"crear() idéntico a menos de {VENTANA_DUPLICADO_SEGUNDOS}s lanza MovimientoDuplicadoError",
        MovimientoDuplicadoError,
        lambda: repo.crear(**campos_comunes),
    )
    total_filas_tras_intento = manager.fetchone(
        "SELECT COUNT(*) AS n FROM movimientos_activo WHERE activo_id = ? AND tipo = 'compra';", (activo_1,)
    )["n"]
    caso("el intento duplicado rechazado NO insertó una segunda fila", 1, total_filas_tras_intento)

    print("\n--- crear() — con tipo distinto ('venta') no cuenta como duplicado ---")
    campos_tipo_distinto = dict(campos_comunes, tipo="venta")
    mov_2 = repo.crear(**campos_tipo_distinto)
    caso("crear() con tipo distinto no dispara el chequeo de duplicado", True, isinstance(mov_2, int))

    print(f"\n--- crear() — tras simular que pasaron {VENTANA_DUPLICADO_SEGUNDOS + 1}s (ajustando creada_en a mano) ---")
    manager.execute(
        "UPDATE movimientos_activo SET creada_en = datetime('now', ?) WHERE id = ?;",
        (f"-{VENTANA_DUPLICADO_SEGUNDOS + 1} seconds", mov_1),
    )
    mov_3 = repo.crear(**campos_comunes)
    caso("tras la ventana de duplicado, crear() idéntico sí se permite", True, isinstance(mov_3, int))
    total_filas_final = manager.fetchone(
        "SELECT COUNT(*) AS n FROM movimientos_activo WHERE activo_id = ? AND tipo = 'compra';", (activo_1,)
    )["n"]
    caso("ahora sí existen dos movimientos de compra idénticos (mov_1 y mov_3)", 2, total_filas_final)

    manager.desconectar()

    print(f"\n--- Resumen: {casos_ok}/{casos_total} casos OK ---")


if __name__ == "__main__":
    main()
