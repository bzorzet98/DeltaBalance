"""
verify/ahorros/verify_objetivos_ahorro_repository.py

Verifica ObjetivosAhorroRepository
(repositories/objetivos_ahorro_repository.py): crear (con y sin
monto_meta_minor/fecha_meta), obtener_por_id, listar con filtro por
estado, y actualizar con el sentinel NO_CAMBIAR (incluyendo escribir NULL
explícito en monto_meta_minor/fecha_meta, y cambiar estado).

Correlo con:
    python verify/ahorros/verify_objetivos_ahorro_repository.py
"""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from verify._dummy_db import crear_dummy_db
from db.database import DatabaseManager
from repositories.objetivos_ahorro_repository import ObjetivosAhorroRepository


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
    repo = ObjetivosAhorroRepository(manager)

    print("--- crear() — con monto_meta_minor y fecha_meta ---")
    objetivo_1 = repo.crear(nombre="Terreno", monto_meta_minor=50000000, fecha_meta="2028-01-01")
    caso("crear() devuelve un id numérico", True, isinstance(objetivo_1, int))

    fila_1 = manager.fetchone("SELECT * FROM objetivos_ahorro WHERE id = ?;", (objetivo_1,))
    caso("crear() persiste monto_meta_minor", 50000000, fila_1["monto_meta_minor"])
    caso("crear() persiste fecha_meta", "2028-01-01", fila_1["fecha_meta"])
    caso("crear() deja estado='activo' por default de columna", "activo", fila_1["estado"])

    print("\n--- crear() — sin monto_meta_minor ni fecha_meta ---")
    objetivo_2 = repo.crear(nombre="Vacaciones")
    fila_2 = manager.fetchone("SELECT * FROM objetivos_ahorro WHERE id = ?;", (objetivo_2,))
    caso("crear() sin monto_meta_minor lo deja NULL", None, fila_2["monto_meta_minor"])
    caso("crear() sin fecha_meta la deja NULL", None, fila_2["fecha_meta"])

    objetivo_3 = repo.crear(nombre="Moto")

    print("\n--- obtener_por_id() ---")
    caso("obtener_por_id() encuentra el objetivo 1", "Terreno", repo.obtener_por_id(objetivo_1)["nombre"])
    caso("obtener_por_id() de un id inexistente devuelve None", None, repo.obtener_por_id(999999))

    print("\n--- listar() — filtro por estado ---")
    todos_activos = repo.listar(estado="activo")
    ids_activos = [o["id"] for o in todos_activos]
    caso("listar(estado='activo') incluye los 3 objetivos (todos activos por default)", True, all(o in ids_activos for o in (objetivo_1, objetivo_2, objetivo_3)))

    repo.actualizar(objetivo_2, estado="cumplido")
    solo_cumplidos = repo.listar(estado="cumplido")
    ids_cumplidos = [o["id"] for o in solo_cumplidos]
    caso("listar(estado='cumplido') incluye objetivo_2 tras el cambio de estado", True, objetivo_2 in ids_cumplidos)
    caso("listar(estado='cumplido') excluye objetivo_1 y objetivo_3", False, objetivo_1 in ids_cumplidos or objetivo_3 in ids_cumplidos)

    listado_sin_filtro = repo.listar()
    caso("listar() sin filtro devuelve los 3 objetivos", True, all(o in [x["id"] for x in listado_sin_filtro] for o in (objetivo_1, objetivo_2, objetivo_3)))

    print("\n--- actualizar() — sentinel NO_CAMBIAR ---")
    repo.actualizar(objetivo_1, nombre="Terreno (Villa)")
    fila_1_v2 = repo.obtener_por_id(objetivo_1)
    caso("actualizar(nombre=...) solo: nombre cambia", "Terreno (Villa)", fila_1_v2["nombre"])
    caso("actualizar(nombre=...) solo: monto_meta_minor mantiene su valor previo (NO_CAMBIAR)", 50000000, fila_1_v2["monto_meta_minor"])
    caso("actualizar(nombre=...) solo: fecha_meta mantiene su valor previo (NO_CAMBIAR)", "2028-01-01", fila_1_v2["fecha_meta"])
    caso("actualizar(nombre=...) solo: estado mantiene su valor previo (NO_CAMBIAR)", "activo", fila_1_v2["estado"])

    print("\n--- actualizar() — None explícito escribe NULL ---")
    repo.actualizar(objetivo_1, monto_meta_minor=None, fecha_meta=None)
    fila_1_v3 = repo.obtener_por_id(objetivo_1)
    caso("actualizar(monto_meta_minor=None) escribe NULL explícito", None, fila_1_v3["monto_meta_minor"])
    caso("actualizar(fecha_meta=None) escribe NULL explícito", None, fila_1_v3["fecha_meta"])
    caso("actualizar(monto_meta_minor=None, fecha_meta=None) no toca nombre", "Terreno (Villa)", fila_1_v3["nombre"])

    sin_cambios = repo.actualizar(objetivo_3)
    caso("actualizar() sin ningún campo devuelve False", False, sin_cambios)

    print("\n--- actualizar(conn=...) — participa de una transacción externa ---")
    conn_externo = manager.conn
    with manager.transaction():
        repo.actualizar(objetivo_3, estado="cancelado", conn=conn_externo)
    caso("actualizar(conn=...) persiste tras comitear", "cancelado", repo.obtener_por_id(objetivo_3)["estado"])

    manager.desconectar()

    print(f"\n--- Resumen: {casos_ok}/{casos_total} casos OK ---")


if __name__ == "__main__":
    main()
