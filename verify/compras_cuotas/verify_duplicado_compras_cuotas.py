"""
verify/compras_cuotas/verify_duplicado_compras_cuotas.py

Verifica el chequeo de duplicados de ComprasCuotasRepository.crear()
(repositories/compras_cuotas_repository.py, Parte A de la tarea de
seguridad "bloqueo de duplicados"): crear() dos veces seguidas con los
mismos cuenta_id/monto_total_minor/fecha_compra/concepto en menos de
VENTANA_DUPLICADO_SEGUNDOS lanza CompraDuplicadaError la segunda vez;
después de esa ventana (simulada ajustando creada_en a mano) sí permite
crear una compra idéntica.

Correlo con:
    python verify/compras_cuotas/verify_duplicado_compras_cuotas.py
"""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from verify._dummy_db import crear_dummy_db
from db.database import DatabaseManager
from repositories.cuentas_repository import CuentasRepository
from repositories.compras_cuotas_repository import (
    ComprasCuotasRepository,
    CompraDuplicadaError,
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
    cuentas_repo = CuentasRepository(manager)
    repo = ComprasCuotasRepository(manager)

    tarjeta = cuentas_repo.crear(nombre="Tarjeta Dup Compras", tipo="credito", moneda_codigo="ARS")
    moneda_ars = manager.fetchone("SELECT id FROM monedas WHERE codigo = 'ARS';")["id"]
    cat_egreso = manager.fetchone("SELECT id FROM categorias WHERE tipo = 'egreso' LIMIT 1;")["id"]

    campos_comunes = dict(
        fecha_compra="2026-01-10",
        concepto="Lavarropas duplicado",
        cuenta_id=tarjeta,
        categoria_id=cat_egreso,
        moneda_id=moneda_ars,
        monto_total_minor=36000000,
        total_cuotas=12,
        monto_por_cuota_minor=3000000,
    )

    print("--- crear() — primera vez, sin duplicado previo ---")
    compra_1 = repo.crear(**campos_comunes)
    caso("primera creación devuelve un id numérico", True, isinstance(compra_1, int))

    print("\n--- crear() — segunda vez idéntica, dentro de la ventana de 5s ---")
    caso_excepcion(
        f"crear() idéntico a menos de {VENTANA_DUPLICADO_SEGUNDOS}s lanza CompraDuplicadaError",
        CompraDuplicadaError,
        lambda: repo.crear(**campos_comunes),
    )
    total_filas_tras_intento = manager.fetchone(
        "SELECT COUNT(*) AS n FROM compras_cuotas WHERE concepto = ?;", (campos_comunes["concepto"],)
    )["n"]
    caso("el intento duplicado rechazado NO insertó una segunda fila", 1, total_filas_tras_intento)

    print("\n--- crear() — con fecha_compra distinta no cuenta como duplicado ---")
    campos_fecha_distinta = dict(campos_comunes, fecha_compra="2026-01-11")
    compra_2 = repo.crear(**campos_fecha_distinta)
    caso("crear() con fecha distinta no dispara el chequeo de duplicado", True, isinstance(compra_2, int))

    print(f"\n--- crear() — tras simular que pasaron {VENTANA_DUPLICADO_SEGUNDOS + 1}s (ajustando creada_en a mano) ---")
    manager.execute(
        "UPDATE compras_cuotas SET creada_en = datetime('now', ?) WHERE id = ?;",
        (f"-{VENTANA_DUPLICADO_SEGUNDOS + 1} seconds", compra_1),
    )
    compra_3 = repo.crear(**campos_comunes)
    caso("tras la ventana de duplicado, crear() idéntico sí se permite", True, isinstance(compra_3, int))
    total_filas_final = manager.fetchone(
        "SELECT COUNT(*) AS n FROM compras_cuotas WHERE concepto = ?;", (campos_comunes["concepto"],)
    )["n"]
    caso("ahora sí existen dos compras idénticas (compra_1 y compra_3)", 2, total_filas_final)

    manager.desconectar()

    print(f"\n--- Resumen: {casos_ok}/{casos_total} casos OK ---")


if __name__ == "__main__":
    main()
