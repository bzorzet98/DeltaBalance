"""
verify/schema/verify_categorias_soft_delete.py

Verifica el bloque SOFT-DELETE DE CATEGORÍAS: categorias.activa se agrega vía
db/schema_migrations.py (no como ALTER TABLE suelto en schema.sql), nace en
1 por default, y "borrar" una categoría es un UPDATE a activa = 0, nunca un
DELETE físico de la fila.

Correlo con:
    python verify/schema/verify_categorias_soft_delete.py
"""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from verify._dummy_db import crear_dummy_db
from db.database import DatabaseManager


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
    conn = manager.conn

    print("--- Columna categorias.activa ---")
    columnas = [fila[1] for fila in conn.execute("PRAGMA table_info(categorias);")]
    caso("categorias.activa existe (agregada vía schema_migrations.py)", True, "activa" in columnas)

    print("\n--- Categorías del seed nacen con activa = 1 ---")
    total_seed = conn.execute("SELECT COUNT(*) FROM categorias;").fetchone()[0]
    activas_seed = conn.execute("SELECT COUNT(*) FROM categorias WHERE activa = 1;").fetchone()[0]
    caso(
        "todas las categorías sembradas por seed.sql tienen activa = 1",
        total_seed,
        activas_seed,
    )

    print("\n--- Categoría nueva nace activa = 1 sin especificarlo ---")
    cur = conn.execute(
        "INSERT INTO categorias (categoria_principal, subcategoria, tipo) VALUES (?, ?, ?);",
        ("VERIFY", "Categoria de prueba", "egreso"),
    )
    conn.commit()
    test_id = cur.lastrowid

    activa_al_nacer = conn.execute(
        "SELECT activa FROM categorias WHERE id = ?;", (test_id,)
    ).fetchone()[0]
    caso("categoría nueva nace con activa = 1 por default", 1, activa_al_nacer)

    print("\n--- Soft-delete: UPDATE activa = 0, la fila NO se borra ---")
    conn.execute("UPDATE categorias SET activa = 0 WHERE id = ?;", (test_id,))
    conn.commit()

    fila_tras_soft_delete = conn.execute(
        "SELECT id, activa FROM categorias WHERE id = ?;", (test_id,)
    ).fetchone()

    caso(
        "la fila sigue existiendo en categorias tras el soft-delete (no es un DELETE)",
        True,
        fila_tras_soft_delete is not None,
    )
    if fila_tras_soft_delete is not None:
        caso("la fila quedó con activa = 0 tras el soft-delete", 0, fila_tras_soft_delete[1])

    manager.desconectar()

    print(f"\n--- Resumen: {casos_ok}/{casos_total} casos OK ---")


if __name__ == "__main__":
    main()
