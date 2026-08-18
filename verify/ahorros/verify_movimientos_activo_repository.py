"""
verify/ahorros/verify_movimientos_activo_repository.py

Verifica MovimientosActivoRepository
(repositories/movimientos_activo_repository.py): crear (tipo compra/venta/
rendimiento), obtener_por_id, listar_por_activo/listar_por_tipo con
filtros cruzados, y — el caso que más importa de este bloque — la
atomicidad REAL de registrar una compra junto con su asignación inicial a
un objetivo de ahorro: MovimientosActivoRepository.crear(conn=...) +
AsignacionesRepository.crear(conn=...) en la misma transacción externa,
con éxito y con rollback simulado.

Correlo con:
    python verify/ahorros/verify_movimientos_activo_repository.py
"""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from verify._dummy_db import crear_dummy_db
from db.database import DatabaseManager
from repositories.activos_financieros_repository import ActivosFinancierosRepository
from repositories.objetivos_ahorro_repository import ObjetivosAhorroRepository
from repositories.movimientos_activo_repository import MovimientosActivoRepository
from repositories.asignaciones_repository import AsignacionesRepository


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
    activos_repo = ActivosFinancierosRepository(manager)
    objetivos_repo = ObjetivosAhorroRepository(manager)
    repo = MovimientosActivoRepository(manager)
    asignaciones_repo = AsignacionesRepository(manager)

    moneda_usd = manager.fetchone("SELECT id FROM monedas WHERE codigo = 'USD';")["id"]
    activo_1 = activos_repo.crear(nombre="SPY", tipo="accion", moneda_id=moneda_usd)
    activo_2 = activos_repo.crear(nombre="QQQ", tipo="accion", moneda_id=moneda_usd)
    objetivo_1 = objetivos_repo.crear(nombre="Terreno")

    print("--- crear() — tipo 'compra' ---")
    mov_1 = repo.crear(
        activo_id=activo_1, tipo="compra", fecha="2026-01-05",
        monto_total_minor=100000000, cantidad=10.5, precio_unitario_minor=9523810,
        dolar_oficial_momento_minor=100000,
    )
    caso("crear() devuelve un id numérico", True, isinstance(mov_1, int))

    fila_1 = manager.fetchone("SELECT * FROM movimientos_activo WHERE id = ?;", (mov_1,))
    caso("crear() persiste tipo='compra'", "compra", fila_1["tipo"])
    caso("crear() persiste cantidad", 10.5, fila_1["cantidad"])
    caso("crear() persiste monto_total_minor", 100000000, fila_1["monto_total_minor"])
    caso("crear() persiste dolar_oficial_momento_minor", 100000, fila_1["dolar_oficial_momento_minor"])

    print("\n--- crear() — tipo 'venta' y 'rendimiento' ---")
    mov_2 = repo.crear(activo_id=activo_1, tipo="venta", fecha="2026-02-01", monto_total_minor=50000000, cantidad=5.0)
    mov_3 = repo.crear(activo_id=activo_1, tipo="rendimiento", fecha="2026-03-01", monto_total_minor=2000000)
    caso("crear() tipo='rendimiento' sin cantidad/precio_unitario los deja NULL", True, manager.fetchone("SELECT cantidad, precio_unitario_minor FROM movimientos_activo WHERE id = ?;", (mov_3,))["cantidad"] is None)

    mov_activo_2 = repo.crear(activo_id=activo_2, tipo="compra", fecha="2026-01-10", monto_total_minor=30000000, cantidad=3.0)

    print("\n--- obtener_por_id() ---")
    caso("obtener_por_id() encuentra el movimiento 1", "compra", repo.obtener_por_id(mov_1)["tipo"])
    caso("obtener_por_id() de un id inexistente devuelve None", None, repo.obtener_por_id(999999))

    print("\n--- listar_por_activo() — filtro cruzado ---")
    movimientos_activo_1 = repo.listar_por_activo(activo_1)
    ids_activo_1 = [m["id"] for m in movimientos_activo_1]
    caso("listar_por_activo(activo_1) incluye mov_1, mov_2 y mov_3", True, all(m in ids_activo_1 for m in (mov_1, mov_2, mov_3)))
    caso("listar_por_activo(activo_1) excluye el movimiento de activo_2", False, mov_activo_2 in ids_activo_1)

    movimientos_activo_2 = repo.listar_por_activo(activo_2)
    caso("listar_por_activo(activo_2) solo trae su propio movimiento", [mov_activo_2], [m["id"] for m in movimientos_activo_2])

    print("\n--- listar_por_tipo() — filtro cruzado ---")
    compras_activo_1 = repo.listar_por_tipo(activo_1, "compra")
    caso("listar_por_tipo(activo_1, 'compra') incluye mov_1", True, mov_1 in [m["id"] for m in compras_activo_1])
    caso("listar_por_tipo(activo_1, 'compra') excluye mov_2 (venta) y mov_3 (rendimiento)", False, mov_2 in [m["id"] for m in compras_activo_1] or mov_3 in [m["id"] for m in compras_activo_1])

    ventas_activo_1 = repo.listar_por_tipo(activo_1, "venta")
    caso("listar_por_tipo(activo_1, 'venta') trae exactamente mov_2", [mov_2], [m["id"] for m in ventas_activo_1])

    print("\n--- crear() + AsignacionesRepository.crear() — atomicidad real (compra + asignación inicial) ---")
    conn_externo = manager.conn
    with manager.transaction():
        mov_4 = repo.crear(
            activo_id=activo_1, tipo="compra", fecha="2026-04-01",
            monto_total_minor=20000000, cantidad=2.0,
            conn=conn_externo,
        )
        asignacion_id = asignaciones_repo.crear(
            movimiento_id=mov_4, objetivo_id=objetivo_1,
            porcentaje=100.0, monto_asignado_minor=20000000,
            conn=conn_externo,
        )
    caso("crear() con conn en transacción exitosa: el movimiento persiste", "compra", repo.obtener_por_id(mov_4)["tipo"])
    caso("AsignacionesRepository.crear() con conn en la misma transacción: la asignación persiste", 1, len(asignaciones_repo.listar_por_movimiento(mov_4)))
    caso("la asignación persistida apunta al movimiento correcto", mov_4, asignaciones_repo.listar_por_movimiento(mov_4)[0]["movimiento_id"])

    print("\n--- crear() + AsignacionesRepository.crear() — rollback simulado ---")
    movimientos_antes = manager.fetchone("SELECT COUNT(*) AS n FROM movimientos_activo;")["n"]
    asignaciones_antes = manager.fetchone("SELECT COUNT(*) AS n FROM asignaciones;")["n"]
    try:
        with manager.transaction():
            mov_5 = repo.crear(
                activo_id=activo_1, tipo="compra", fecha="2026-05-01",
                monto_total_minor=5000000, cantidad=0.5,
                conn=conn_externo,
            )
            asignaciones_repo.crear(
                movimiento_id=mov_5, objetivo_id=objetivo_1,
                porcentaje=100.0, monto_asignado_minor=5000000,
                conn=conn_externo,
            )
            raise RuntimeError("Fallo simulado a mitad de la transacción externa")
    except RuntimeError:
        pass
    movimientos_despues = manager.fetchone("SELECT COUNT(*) AS n FROM movimientos_activo;")["n"]
    asignaciones_despues = manager.fetchone("SELECT COUNT(*) AS n FROM asignaciones;")["n"]
    caso("rollback revierte el INSERT del movimiento (no queda huérfano)", movimientos_antes, movimientos_despues)
    caso("rollback revierte también la asignación (ninguna queda huérfana del otro)", asignaciones_antes, asignaciones_despues)

    manager.desconectar()

    print(f"\n--- Resumen: {casos_ok}/{casos_total} casos OK ---")


if __name__ == "__main__":
    main()
