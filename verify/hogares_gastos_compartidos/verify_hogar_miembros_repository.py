"""
verify/hogares_gastos_compartidos/verify_hogar_miembros_repository.py

Verifica HogarMiembrosRepository
(repositories/hogar_miembros_repository.py): agregar() (con y sin
porcentaje_default, y la PRIMARY KEY compuesta (hogar_id, usuario_local)
subiendo sqlite3.IntegrityError sin envolver ante un duplicado — ver
docstring del módulo), listar_miembros()/obtener_miembro() con filtros
cruzados, y actualizar_porcentaje_default() (incluyendo escribir None
explícito y conn+commit externo).

Correlo con:
    python verify/hogares_gastos_compartidos/verify_hogar_miembros_repository.py
"""

from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from verify._dummy_db import crear_dummy_db
from db.database import DatabaseManager
from repositories.hogares_repository import HogaresRepository
from repositories.hogar_miembros_repository import HogarMiembrosRepository


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
    hogares_repo = HogaresRepository(manager)
    repo = HogarMiembrosRepository(manager)

    hogar_1 = hogares_repo.crear(codigo_invitacion="HOG001", nombre="Hogar 1")
    hogar_2 = hogares_repo.crear(codigo_invitacion="HOG002", nombre="Hogar 2")

    print("--- agregar() ---")
    repo.agregar(hogar_id=hogar_1, usuario_local="bruno", porcentaje_default=60.0)
    repo.agregar(hogar_id=hogar_1, usuario_local="martina", porcentaje_default=40.0)
    repo.agregar(hogar_id=hogar_2, usuario_local="bruno")  # sin porcentaje_default

    miembro_bruno_hogar1 = repo.obtener_miembro(hogar_1, "bruno")
    caso("agregar() persiste porcentaje_default", 60.0, miembro_bruno_hogar1["porcentaje_default"])

    miembro_bruno_hogar2 = repo.obtener_miembro(hogar_2, "bruno")
    caso("agregar() sin porcentaje_default lo deja NULL", None, miembro_bruno_hogar2["porcentaje_default"])

    caso(
        "'bruno' puede ser miembro de dos hogares distintos (la PK es hogar_id+usuario_local, no usuario_local solo)",
        True,
        miembro_bruno_hogar1 is not None and miembro_bruno_hogar2 is not None,
    )

    print("\n--- agregar() — PRIMARY KEY compuesta sube sin envolver ---")
    caso_excepcion(
        "agregar() con (hogar_id, usuario_local) duplicado lanza sqlite3.IntegrityError sin envolver",
        sqlite3.IntegrityError,
        lambda: repo.agregar(hogar_id=hogar_1, usuario_local="bruno", porcentaje_default=99.0),
    )
    caso(
        "el intento duplicado no pisó el porcentaje_default original (sigue en 60.0)",
        60.0,
        repo.obtener_miembro(hogar_1, "bruno")["porcentaje_default"],
    )

    print("\n--- listar_miembros() — filtro cruzado ---")
    miembros_hogar1 = repo.listar_miembros(hogar_1)
    usuarios_hogar1 = [m["usuario_local"] for m in miembros_hogar1]
    caso("listar_miembros(hogar_1) incluye a bruno y martina", True, "bruno" in usuarios_hogar1 and "martina" in usuarios_hogar1)
    caso("listar_miembros(hogar_1) trae exactamente 2 miembros", 2, len(miembros_hogar1))

    miembros_hogar2 = repo.listar_miembros(hogar_2)
    caso(
        "listar_miembros(hogar_2) trae exactamente 1 miembro (bruno, sin martina)",
        ["bruno"],
        [m["usuario_local"] for m in miembros_hogar2],
    )

    print("\n--- obtener_miembro() ---")
    caso("obtener_miembro() de una combinación inexistente devuelve None", None, repo.obtener_miembro(hogar_1, "no_existe"))
    caso("obtener_miembro() de un hogar_id inexistente devuelve None", None, repo.obtener_miembro(999999, "bruno"))

    print("\n--- actualizar_porcentaje_default() ---")
    repo.actualizar_porcentaje_default(hogar_1, "martina", 45.0)
    caso("actualizar_porcentaje_default() reescribe el valor", 45.0, repo.obtener_miembro(hogar_1, "martina")["porcentaje_default"])

    repo.actualizar_porcentaje_default(hogar_1, "martina", None)
    caso("actualizar_porcentaje_default(None) escribe NULL explícito", None, repo.obtener_miembro(hogar_1, "martina")["porcentaje_default"])

    print("\n--- actualizar_porcentaje_default(conn=...) — participa de una transacción externa ---")
    conn_externo = manager.conn
    with manager.transaction():
        repo.actualizar_porcentaje_default(hogar_2, "bruno", 100.0, conn=conn_externo)
    caso(
        "actualizar_porcentaje_default(conn=...) persiste tras comitear",
        100.0,
        repo.obtener_miembro(hogar_2, "bruno")["porcentaje_default"],
    )

    manager.desconectar()

    print(f"\n--- Resumen: {casos_ok}/{casos_total} casos OK ---")


if __name__ == "__main__":
    main()
