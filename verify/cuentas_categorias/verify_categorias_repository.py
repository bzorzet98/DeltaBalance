"""
verify/cuentas_categorias/verify_categorias_repository.py

Verifica CategoriasRepository (repositories/categorias_repository.py): que
el CRUD extraído de db/database.py se comporta igual que los métodos viejos
(obtener_categorias, crear_categoria) más los dos métodos nuevos de
soft-delete (desactivar/activar) que antes no existían — crear, obtener por
id, listar filtrando por tipo, y que listar() respeta el filtro de activa
por default y con incluir_inactivas=True.

Correlo con:
    python verify/cuentas_categorias/verify_categorias_repository.py
"""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from verify._dummy_db import crear_dummy_db
from db.database import DatabaseManager
from repositories.categorias_repository import CategoriasRepository


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
    manager.inicializar()  # aplica también schema_migrations.py -> categorias.activa
    repo = CategoriasRepository(manager)

    print("--- crear() ---")
    categoria_id = repo.crear("VERIFY", "Categoria Repo Test", "egreso")
    caso("crear() devuelve un id numérico", True, isinstance(categoria_id, int) and categoria_id > 0)

    print("\n--- obtener_por_id() ---")
    fila = repo.obtener_por_id(categoria_id)
    caso("obtener_por_id() encuentra la categoría recién creada", "Categoria Repo Test", fila["subcategoria"] if fila else None)
    caso("la categoría nace activa = 1", 1, fila["activa"] if fila else None)

    print("\n--- listar() filtrando por tipo ---")
    listado_egreso = repo.listar(tipo="egreso")
    caso("listar(tipo='egreso') incluye la categoría recién creada", True, categoria_id in [r["id"] for r in listado_egreso])
    listado_ingreso = repo.listar(tipo="ingreso")
    caso("listar(tipo='ingreso') NO incluye una categoría de tipo egreso", False, categoria_id in [r["id"] for r in listado_ingreso])

    print("\n--- listar() respeta el filtro de activa por default ---")
    listado_default = repo.listar()
    caso("listar() sin argumentos incluye la categoría (nace activa)", True, categoria_id in [r["id"] for r in listado_default])

    print("\n--- desactivar() (soft-delete) ---")
    repo.desactivar(categoria_id)
    listado_tras_desactivar = repo.listar()
    caso(
        "tras desactivar(), listar() default ya no incluye la categoría",
        False,
        categoria_id in [r["id"] for r in listado_tras_desactivar],
    )
    listado_incluir_inactivas = repo.listar(incluir_inactivas=True)
    caso(
        "listar(incluir_inactivas=True) sigue mostrando la categoría desactivada",
        True,
        categoria_id in [r["id"] for r in listado_incluir_inactivas],
    )
    fila_desactivada = repo.obtener_por_id(categoria_id)
    caso("la fila sigue existiendo en la tabla tras desactivar() (no es un DELETE)", True, fila_desactivada is not None)
    caso("la fila desactivada quedó con activa = 0", 0, fila_desactivada["activa"] if fila_desactivada else None)

    print("\n--- activar() (caso inverso) ---")
    repo.activar(categoria_id)
    fila_reactivada = repo.obtener_por_id(categoria_id)
    caso("activar() deja la fila con activa = 1 otra vez", 1, fila_reactivada["activa"] if fila_reactivada else None)
    listado_tras_activar = repo.listar()
    caso("tras activar(), listar() default vuelve a incluir la categoría", True, categoria_id in [r["id"] for r in listado_tras_activar])

    manager.desconectar()

    print(f"\n--- Resumen: {casos_ok}/{casos_total} casos OK ---")


if __name__ == "__main__":
    main()
