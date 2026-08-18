"""
verify/ahorros/verify_activos_financieros_repository.py

Verifica ActivosFinancierosRepository
(repositories/activos_financieros_repository.py): crear, obtener_por_id,
listar con filtros cruzados (tipo, solo_activos), actualizar con el
sentinel NO_CAMBIAR, y desactivar()/activar() (soft-delete, mismo patrón
que categorías — actualizar() nunca toca `activa`).

Correlo con:
    python verify/ahorros/verify_activos_financieros_repository.py
"""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from verify._dummy_db import crear_dummy_db
from db.database import DatabaseManager
from repositories.activos_financieros_repository import ActivosFinancierosRepository


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
    repo = ActivosFinancierosRepository(manager)

    moneda_ars = manager.fetchone("SELECT id FROM monedas WHERE codigo = 'ARS';")["id"]
    moneda_usd = manager.fetchone("SELECT id FROM monedas WHERE codigo = 'USD';")["id"]

    print("--- crear() ---")
    activo_1 = repo.crear(nombre="SPY", tipo="accion", moneda_id=moneda_usd)
    activo_2 = repo.crear(nombre="FCI Ahorro Pesos", tipo="fci", moneda_id=moneda_ars)
    activo_3 = repo.crear(nombre="Plazo Fijo Banco X", tipo="plazo_fijo", moneda_id=moneda_ars)
    caso(
        "crear() devuelve ids numéricos distintos",
        True,
        len({activo_1, activo_2, activo_3}) == 3 and all(isinstance(x, int) for x in (activo_1, activo_2, activo_3)),
    )

    fila_cruda = manager.fetchone("SELECT * FROM activos_financieros WHERE id = ?;", (activo_1,))
    caso("crear() persiste nombre", "SPY", fila_cruda["nombre"])
    caso("crear() persiste tipo", "accion", fila_cruda["tipo"])
    caso("crear() deja activa=1 por default de columna", 1, fila_cruda["activa"])

    print("\n--- obtener_por_id() ---")
    caso("obtener_por_id() encuentra el activo 1", "SPY", repo.obtener_por_id(activo_1)["nombre"])
    caso("obtener_por_id() de un id inexistente devuelve None", None, repo.obtener_por_id(999999))

    print("\n--- listar() — filtro por tipo ---")
    solo_fci = repo.listar(tipo="fci")
    ids_fci = [a["id"] for a in solo_fci]
    caso("listar(tipo='fci') incluye activo_2", True, activo_2 in ids_fci)
    caso("listar(tipo='fci') excluye activo_1 (accion) y activo_3 (plazo_fijo)", False, activo_1 in ids_fci or activo_3 in ids_fci)

    print("\n--- listar() — filtro solo_activos ---")
    todos_al_inicio = repo.listar()
    caso("listar() sin filtro de tipo incluye los 3 activos (todos activos por default)", True, all(a in [x["id"] for x in todos_al_inicio] for a in (activo_1, activo_2, activo_3)))

    print("\n--- actualizar() — sentinel NO_CAMBIAR ---")
    repo.actualizar(activo_1, nombre="SPY (renombrado)")
    fila_1 = repo.obtener_por_id(activo_1)
    caso("actualizar(nombre=...) solo: nombre cambia", "SPY (renombrado)", fila_1["nombre"])
    caso("actualizar(nombre=...) solo: tipo mantiene su valor previo (NO_CAMBIAR)", "accion", fila_1["tipo"])
    caso("actualizar(nombre=...) solo: moneda_id mantiene su valor previo (NO_CAMBIAR)", moneda_usd, fila_1["moneda_id"])

    repo.actualizar(activo_1, tipo="cripto", moneda_id=moneda_ars)
    fila_1_v2 = repo.obtener_por_id(activo_1)
    caso("actualizar(tipo=..., moneda_id=...): ambos cambian", True, fila_1_v2["tipo"] == "cripto" and fila_1_v2["moneda_id"] == moneda_ars)
    caso("actualizar(tipo=..., moneda_id=...): nombre mantiene su valor previo", "SPY (renombrado)", fila_1_v2["nombre"])

    sin_cambios = repo.actualizar(activo_1)
    caso("actualizar() sin ningún campo devuelve False", False, sin_cambios)

    print("\n--- actualizar(conn=...) — participa de una transacción externa ---")
    conn_externo = manager.conn
    with manager.transaction():
        repo.actualizar(activo_2, nombre="FCI Ahorro Pesos (v2)", conn=conn_externo)
    caso("actualizar(conn=...) persiste tras comitear", "FCI Ahorro Pesos (v2)", repo.obtener_por_id(activo_2)["nombre"])

    print("\n--- desactivar() / activar() — soft-delete, actualizar() nunca toca activa ---")
    repo.desactivar(activo_3)
    fila_3 = repo.obtener_por_id(activo_3)
    caso("desactivar() deja activa=0", 0, fila_3["activa"])

    solo_activos_tras_desactivar = repo.listar(solo_activos=True)
    ids_activos = [a["id"] for a in solo_activos_tras_desactivar]
    caso("listar(solo_activos=True) excluye el activo desactivado", False, activo_3 in ids_activos)
    caso("listar(solo_activos=True) sigue incluyendo los otros dos", True, activo_1 in ids_activos and activo_2 in ids_activos)

    todos_incluyendo_inactivos = repo.listar(solo_activos=False)
    ids_todos = [a["id"] for a in todos_incluyendo_inactivos]
    caso("listar(solo_activos=False) SÍ incluye el activo desactivado", True, activo_3 in ids_todos)

    repo.activar(activo_3)
    fila_3_reactivado = repo.obtener_por_id(activo_3)
    caso("activar() deja activa=1 de nuevo", 1, fila_3_reactivado["activa"])
    caso("activar() no tocó ningún otro campo (nombre intacto)", "Plazo Fijo Banco X", fila_3_reactivado["nombre"])

    manager.desconectar()

    print(f"\n--- Resumen: {casos_ok}/{casos_total} casos OK ---")


if __name__ == "__main__":
    main()
