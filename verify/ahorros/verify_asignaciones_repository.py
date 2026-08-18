"""
verify/ahorros/verify_asignaciones_repository.py

Verifica AsignacionesRepository (repositories/asignaciones_repository.py):
crear, listar_por_movimiento/listar_por_objetivo con filtros cruzados,
eliminar (DELETE físico), atomicidad con conn+rollback, y — el caso que
más importa de este repositorio — suma_porcentaje_por_movimiento() con
varias asignaciones parciales: un movimiento con 60+40=100 (completo) y
otro con 60+30=90 (parcial, sin llegar a 100). Esta suma es la que un
futuro SavingsService va a usar para validar "no superar 100%" — el propio
schema.sql documenta que esa regla no se puede expresar con un CHECK de
columna (ver comentario arriba de CREATE TABLE asignaciones).

Correlo con:
    python verify/ahorros/verify_asignaciones_repository.py
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
    movimientos_repo = MovimientosActivoRepository(manager)
    repo = AsignacionesRepository(manager)

    moneda_ars = manager.fetchone("SELECT id FROM monedas WHERE codigo = 'ARS';")["id"]
    activo_1 = activos_repo.crear(nombre="FCI Ahorro", tipo="fci", moneda_id=moneda_ars)
    objetivo_1 = objetivos_repo.crear(nombre="Terreno")
    objetivo_2 = objetivos_repo.crear(nombre="Vacaciones")
    objetivo_3 = objetivos_repo.crear(nombre="Moto")

    movimiento_1 = movimientos_repo.crear(activo_id=activo_1, tipo="compra", fecha="2026-01-05", monto_total_minor=100000000)
    movimiento_2 = movimientos_repo.crear(activo_id=activo_1, tipo="compra", fecha="2026-02-05", monto_total_minor=50000000)

    print("--- suma_porcentaje_por_movimiento() — sin ninguna asignación todavía ---")
    caso("suma_porcentaje_por_movimiento() de un movimiento sin asignaciones devuelve 0.0", 0.0, repo.suma_porcentaje_por_movimiento(movimiento_1))

    print("\n--- crear() — movimiento_1: 60% + 40% = 100% (completo) ---")
    asig_1a = repo.crear(movimiento_id=movimiento_1, objetivo_id=objetivo_1, porcentaje=60.0, monto_asignado_minor=60000000)
    asig_1b = repo.crear(movimiento_id=movimiento_1, objetivo_id=objetivo_2, porcentaje=40.0, monto_asignado_minor=40000000)
    caso("crear() devuelve ids numéricos distintos", True, asig_1a != asig_1b)
    caso("suma_porcentaje_por_movimiento(movimiento_1) da 100.0 (60+40)", 100.0, repo.suma_porcentaje_por_movimiento(movimiento_1))

    print("\n--- crear() — movimiento_2: 60% + 30% = 90% (parcial, sin completar) ---")
    asig_2a = repo.crear(movimiento_id=movimiento_2, objetivo_id=objetivo_1, porcentaje=60.0, monto_asignado_minor=30000000)
    asig_2b = repo.crear(movimiento_id=movimiento_2, objetivo_id=objetivo_3, porcentaje=30.0, monto_asignado_minor=15000000)
    caso("suma_porcentaje_por_movimiento(movimiento_2) da 90.0 (60+30, no llega a 100)", 90.0, repo.suma_porcentaje_por_movimiento(movimiento_2))

    print("\n--- listar_por_movimiento() — filtro cruzado ---")
    asignaciones_mov_1 = repo.listar_por_movimiento(movimiento_1)
    ids_mov_1 = [a["id"] for a in asignaciones_mov_1]
    caso("listar_por_movimiento(movimiento_1) incluye asig_1a y asig_1b", True, asig_1a in ids_mov_1 and asig_1b in ids_mov_1)
    caso("listar_por_movimiento(movimiento_1) excluye las asignaciones de movimiento_2", False, asig_2a in ids_mov_1 or asig_2b in ids_mov_1)

    print("\n--- listar_por_objetivo() — filtro cruzado ---")
    asignaciones_objetivo_1 = repo.listar_por_objetivo(objetivo_1)
    ids_objetivo_1 = [a["id"] for a in asignaciones_objetivo_1]
    caso("listar_por_objetivo(objetivo_1) incluye asig_1a (de movimiento_1) y asig_2a (de movimiento_2)", True, asig_1a in ids_objetivo_1 and asig_2a in ids_objetivo_1)
    caso("listar_por_objetivo(objetivo_1) excluye asig_1b (era para objetivo_2)", False, asig_1b in ids_objetivo_1)

    print("\n--- eliminar() — DELETE físico ---")
    repo.eliminar(asig_1b)
    caso("eliminar() se refleja en suma_porcentaje_por_movimiento (baja de 100.0 a 60.0)", 60.0, repo.suma_porcentaje_por_movimiento(movimiento_1))
    asignaciones_mov_1_tras_eliminar = repo.listar_por_movimiento(movimiento_1)
    caso("eliminar() se refleja en listar_por_movimiento (ya no aparece)", False, asig_1b in [a["id"] for a in asignaciones_mov_1_tras_eliminar])

    print("\n--- crear() — UNIQUE(movimiento_id, objetivo_id) respetado por el diseño de los datos de prueba ---")
    # (No se prueba la violación en sí porque eso es un sqlite3.IntegrityError
    # crudo, no un caso de negocio de este repositorio — corresponde a un
    # futuro SavingsService decidir cómo traducirlo, igual que
    # RecibosDuplicadoError hizo explícito el UNIQUE de recibos_sueldo.)
    caso(
        "objetivo_1 recibió asignaciones de dos movimientos distintos sin violar el UNIQUE (combinación movimiento+objetivo distinta cada vez)",
        2,
        len(repo.listar_por_objetivo(objetivo_1)),
    )

    print("\n--- crear(conn=...) — participa de una transacción externa ---")
    conn_externo = manager.conn
    movimiento_3 = movimientos_repo.crear(activo_id=activo_1, tipo="compra", fecha="2026-03-05", monto_total_minor=10000000)
    with manager.transaction():
        asig_3 = repo.crear(movimiento_id=movimiento_3, objetivo_id=objetivo_2, porcentaje=100.0, monto_asignado_minor=10000000, conn=conn_externo)
    caso("crear(conn=...) persiste tras comitear", 100.0, repo.suma_porcentaje_por_movimiento(movimiento_3))

    print("\n--- crear(conn=...) — rollback simulado ---")
    movimiento_4 = movimientos_repo.crear(activo_id=activo_1, tipo="compra", fecha="2026-04-05", monto_total_minor=5000000)
    asignaciones_antes = manager.fetchone("SELECT COUNT(*) AS n FROM asignaciones;")["n"]
    try:
        with manager.transaction():
            repo.crear(movimiento_id=movimiento_4, objetivo_id=objetivo_3, porcentaje=100.0, monto_asignado_minor=5000000, conn=conn_externo)
            raise RuntimeError("Fallo simulado a mitad de la transacción externa")
    except RuntimeError:
        pass
    asignaciones_despues = manager.fetchone("SELECT COUNT(*) AS n FROM asignaciones;")["n"]
    caso("rollback revierte el INSERT de la asignación (no queda huérfana)", asignaciones_antes, asignaciones_despues)
    caso("suma_porcentaje_por_movimiento(movimiento_4) tras el rollback sigue en 0.0", 0.0, repo.suma_porcentaje_por_movimiento(movimiento_4))

    manager.desconectar()

    print(f"\n--- Resumen: {casos_ok}/{casos_total} casos OK ---")


if __name__ == "__main__":
    main()
