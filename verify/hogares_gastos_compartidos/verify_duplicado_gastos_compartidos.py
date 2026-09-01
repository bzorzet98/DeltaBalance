"""
verify/hogares_gastos_compartidos/verify_duplicado_gastos_compartidos.py

Verifica el chequeo de duplicados de GastosCompartidosRepository.crear()
(repositories/gastos_compartidos_repository.py, Tarea 9 Parte B —
docs/PROXIMOS_PASOS.md): crear() dos veces seguidas con el mismo
hogar_id/pagador/monto_base_minor/fecha/origen_tipo en menos de
VENTANA_DUPLICADO_SEGUNDOS lanza GastoCompartidoRecienDuplicadoError la
segunda vez; después de esa ventana (simulada ajustando creada_en a mano)
sí permite crear una fila idéntica. Mismo patrón exacto que
verify/transacciones/verify_duplicado_transacciones.py.

Distingue explícitamente esta excepción de GastoCompartidoDuplicadoError
(la que ya existe en services/shared_expenses_service.py, bloqueo por
origen_tipo+origen_id EXACTO repetido, sin ventana de tiempo) — un caso
con origen_id distinto pero el resto de los campos relevantes idénticos
SÍ dispara esta excepción nueva, aunque no dispararía la otra.

Correlo con:
    python verify/hogares_gastos_compartidos/verify_duplicado_gastos_compartidos.py
"""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from verify._dummy_db import crear_dummy_db
from db.database import DatabaseManager
from repositories.hogares_repository import HogaresRepository
from repositories.gastos_compartidos_repository import (
    GastosCompartidosRepository,
    GastoCompartidoRecienDuplicadoError,
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
    repo = GastosCompartidosRepository(manager)
    hogares_repo = HogaresRepository(manager)

    hogar_id = hogares_repo.crear(codigo_invitacion="DUPGC12345", nombre="Hogar Dup GC")
    cat_id = manager.fetchone("SELECT id FROM categorias WHERE tipo = 'egreso' LIMIT 1;")["id"]

    campos_comunes = dict(
        hogar_id=hogar_id,
        pagador="bruno",
        origen_tipo="transaccion",
        origen_id=7001,
        categoria_id=cat_id,
        monto_base_minor=10000,
        coeficiente_deuda=50.0,
        monto_adeudado_minor=5000,
        fecha="2026-01-10",
    )

    print("--- crear() — primera vez, sin duplicado previo ---")
    gasto_1 = repo.crear(**campos_comunes)
    caso("primera creación devuelve un id numérico", True, isinstance(gasto_1, int))

    print("\n--- crear() — segunda vez idéntica (mismo origen_id), dentro de la ventana de 5s ---")
    caso_excepcion(
        f"crear() idéntico a menos de {VENTANA_DUPLICADO_SEGUNDOS}s lanza GastoCompartidoRecienDuplicadoError",
        GastoCompartidoRecienDuplicadoError,
        lambda: repo.crear(**campos_comunes),
    )
    total_filas_tras_intento = manager.fetchone(
        "SELECT COUNT(*) AS n FROM gastos_compartidos WHERE hogar_id = ?;", (hogar_id,)
    )["n"]
    caso("el intento duplicado rechazado NO insertó una segunda fila", 1, total_filas_tras_intento)

    print(
        "\n--- crear() — con origen_id DISTINTO pero el resto idéntico también cuenta como duplicado "
        "(la ventana no mira origen_id, a diferencia de GastoCompartidoDuplicadoError del service) ---"
    )
    campos_origen_distinto = dict(campos_comunes, origen_id=7002)
    caso_excepcion(
        "crear() con origen_id distinto pero hogar/pagador/monto/fecha/origen_tipo iguales "
        "también lanza GastoCompartidoRecienDuplicadoError",
        GastoCompartidoRecienDuplicadoError,
        lambda: repo.crear(**campos_origen_distinto),
    )

    print("\n--- crear() — con un campo relevante distinto (monto_base_minor) no cuenta como duplicado ---")
    campos_monto_distinto = dict(campos_comunes, origen_id=7003, monto_base_minor=999999)
    gasto_2 = repo.crear(**campos_monto_distinto)
    caso("crear() con monto_base_minor distinto no dispara el chequeo de duplicado", True, isinstance(gasto_2, int))

    print("\n--- crear() — con pagador distinto (mismo resto) no cuenta como duplicado ---")
    campos_pagador_distinto = dict(campos_comunes, origen_id=7004, pagador="martina")
    gasto_3 = repo.crear(**campos_pagador_distinto)
    caso("crear() con pagador distinto no dispara el chequeo de duplicado", True, isinstance(gasto_3, int))

    print(f"\n--- crear() — tras simular que pasaron {VENTANA_DUPLICADO_SEGUNDOS + 1}s (ajustando creada_en a mano) ---")
    manager.execute(
        "UPDATE gastos_compartidos SET creada_en = datetime('now', ?) WHERE id = ?;",
        (f"-{VENTANA_DUPLICADO_SEGUNDOS + 1} seconds", gasto_1),
    )
    campos_tras_ventana = dict(campos_comunes, origen_id=7005)
    gasto_4 = repo.crear(**campos_tras_ventana)
    caso("tras la ventana de duplicado, crear() con los mismos campos relevantes sí se permite", True, isinstance(gasto_4, int))

    manager.desconectar()

    print(f"\n--- Resumen: {casos_ok}/{casos_total} casos OK ---")


if __name__ == "__main__":
    main()
