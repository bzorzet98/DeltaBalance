"""
verify/hogares_gastos_compartidos/verify_hogares_repository.py

Verifica HogaresRepository (repositories/hogares_repository.py): crear()
con y sin nombre, obtener_por_id()/obtener_por_codigo(), el UNIQUE de
codigo_invitacion subiendo sin envolver (sqlite3.IntegrityError crudo,
ver docstring del módulo — no hay comportamiento propio que "traducir"
todavía, eso es trabajo del futuro service), y crear(conn=...)
participando de una transacción externa.

Correlo con:
    python verify/hogares_gastos_compartidos/verify_hogares_repository.py
"""

from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from verify._dummy_db import crear_dummy_db
from db.database import DatabaseManager
from repositories.hogares_repository import HogaresRepository


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
    repo = HogaresRepository(manager)

    print("--- crear() ---")
    hogar_1 = repo.crear(codigo_invitacion="ABC123", nombre="Casa Zorzet")
    hogar_2 = repo.crear(codigo_invitacion="XYZ789")  # sin nombre
    caso("crear() devuelve ids numéricos distintos", True, hogar_1 != hogar_2 and isinstance(hogar_1, int))

    print("\n--- obtener_por_id() ---")
    fila_1 = repo.obtener_por_id(hogar_1)
    caso("obtener_por_id() encuentra el hogar 1", "Casa Zorzet", fila_1["nombre"])
    caso("obtener_por_id() persiste codigo_invitacion", "ABC123", fila_1["codigo_invitacion"])
    caso("obtener_por_id() de un id inexistente devuelve None", None, repo.obtener_por_id(999999))

    fila_2 = repo.obtener_por_id(hogar_2)
    caso("crear() sin nombre lo deja NULL", None, fila_2["nombre"])

    print("\n--- obtener_por_codigo() ---")
    caso("obtener_por_codigo() encuentra el hogar por su código", hogar_1, repo.obtener_por_codigo("ABC123")["id"])
    caso("obtener_por_codigo() de un código inexistente devuelve None", None, repo.obtener_por_codigo("NOEXISTE"))

    print("\n--- UNIQUE(codigo_invitacion) — sube sin envolver ---")
    caso_excepcion(
        "crear() con un codigo_invitacion duplicado lanza sqlite3.IntegrityError sin envolver",
        sqlite3.IntegrityError,
        lambda: repo.crear(codigo_invitacion="ABC123", nombre="Otro hogar"),
    )

    print("\n--- crear(conn=...) — participa de una transacción externa ---")
    conn_externo = manager.conn
    with manager.transaction():
        hogar_3 = repo.crear(codigo_invitacion="QWE456", nombre="Hogar atómico", conn=conn_externo)
    caso("crear(conn=...) persiste tras comitear", "Hogar atómico", repo.obtener_por_id(hogar_3)["nombre"])

    manager.desconectar()

    print(f"\n--- Resumen: {casos_ok}/{casos_total} casos OK ---")


if __name__ == "__main__":
    main()
