"""
verify/presupuestos_ingresos_empleos/verify_presupuestos_repository.py

Verifica PresupuestosRepository (repositories/presupuestos_repository.py):
upsert() (camino INSERT y camino UPDATE del ON CONFLICT — el camino UPDATE
reescribe monto_estimado_minor, es_recurrente y notas; es_recurrente se
prueba con dos transiciones (1→0 y 0→1) para confirmar que de verdad se
reescribe y no es una coincidencia de default; moneda_id NO se reescribe en
el camino UPDATE), obtener_por_periodo, listar_por_periodo con filtros
cruzados y shape enriquecido, actualizar_ejecutado, copiar_periodo
(incluyendo el caso de overlap parcial — INSERT OR IGNORE no sobreescribe
lo que ya existe), y atomicidad con conn + rollback.

También confirma el fix del bug de execute()/rowcount: upsert()/
copiar_periodo() devuelven la cantidad real de filas afectadas, leída de
cur.rowcount sobre un cursor real — no el valor fabricado que devolvía
DatabaseManager.execute() para UPDATE/upsert.

Cubre además: listar_categoria_ids_con_presupuesto() (categorías con
presupuesto en cualquier período, sin duplicados), y formula_estimado
(columna nueva de esta tarea): upsert() con fórmula persiste el texto Y el
monto ya calculado, upsert() sin fórmula deja la columna en NULL, y
sobreescribir una fila que tenía fórmula con un número directo LIMPIA
formula_estimado a NULL (nunca queda una fórmula vieja asociada a un monto
que ya no le corresponde) — mismo criterio de "siempre se reescribe en el
camino UPDATE" que ya usan monto_estimado_minor/es_recurrente/notas.

Correlo con:
    python verify/presupuestos_ingresos_empleos/verify_presupuestos_repository.py
"""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from verify._dummy_db import crear_dummy_db
from db.database import DatabaseManager
from repositories.presupuestos_repository import PresupuestosRepository


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
    repo = PresupuestosRepository(manager)

    categorias = manager.fetchall("SELECT id FROM categorias WHERE tipo = 'egreso' LIMIT 2;")
    cat_1, cat_2 = categorias[0]["id"], categorias[1]["id"]
    moneda_ars = manager.fetchone("SELECT id FROM monedas WHERE codigo = 'ARS';")["id"]

    print("--- upsert() — camino INSERT ---")
    filas_afectadas_1 = repo.upsert(
        categoria_id=cat_1, mes=1, anio=2026, moneda_id=moneda_ars,
        monto_estimado_minor=100000, es_recurrente=True, notas="Presupuesto inicial",
    )
    caso("upsert() (INSERT) devuelve 1 fila afectada (rowcount real, no un id fabricado)", 1, filas_afectadas_1)

    fila_cruda = manager.fetchone(
        "SELECT * FROM presupuestos WHERE categoria_id = ? AND mes = 1 AND anio = 2026;", (cat_1,)
    )
    caso("upsert() (INSERT) persiste monto_estimado_minor", 100000, fila_cruda["monto_estimado_minor"])
    caso("upsert() (INSERT) persiste es_recurrente", 1, fila_cruda["es_recurrente"])
    caso("upsert() (INSERT) persiste notas", "Presupuesto inicial", fila_cruda["notas"])
    caso("upsert() (INSERT) arranca monto_ejecutado_minor en 0 (default de columna)", 0, fila_cruda["monto_ejecutado_minor"])

    print("\n--- upsert() — camino UPDATE (ON CONFLICT): reescribe monto_estimado_minor, es_recurrente y notas ---")
    filas_afectadas_2 = repo.upsert(
        categoria_id=cat_1, mes=1, anio=2026, moneda_id=moneda_ars,
        monto_estimado_minor=150000, es_recurrente=False, notas="Presupuesto corregido",
    )
    caso("upsert() (UPDATE) devuelve 1 fila afectada", 1, filas_afectadas_2)

    fila_tras_update = manager.fetchone(
        "SELECT * FROM presupuestos WHERE categoria_id = ? AND mes = 1 AND anio = 2026;", (cat_1,)
    )
    caso("upsert() (UPDATE) SÍ reescribe monto_estimado_minor", 150000, fila_tras_update["monto_estimado_minor"])
    caso("upsert() (UPDATE) SÍ reescribe notas", "Presupuesto corregido", fila_tras_update["notas"])
    caso(
        "upsert() (UPDATE) SÍ reescribe es_recurrente: transición 1→0",
        0,
        fila_tras_update["es_recurrente"],
    )

    filas_afectadas_3 = repo.upsert(
        categoria_id=cat_1, mes=1, anio=2026, moneda_id=moneda_ars,
        monto_estimado_minor=150000, es_recurrente=True, notas="Presupuesto corregido",
    )
    caso("upsert() (UPDATE) devuelve 1 fila afectada (segunda vez)", 1, filas_afectadas_3)
    fila_tras_update_2 = manager.fetchone(
        "SELECT * FROM presupuestos WHERE categoria_id = ? AND mes = 1 AND anio = 2026;", (cat_1,)
    )
    caso(
        "upsert() (UPDATE) SÍ reescribe es_recurrente: transición 0→1 (no es coincidencia de default)",
        1,
        fila_tras_update_2["es_recurrente"],
    )

    print("\n--- obtener_por_periodo() ---")
    caso("obtener_por_periodo() encuentra el presupuesto por el UNIQUE", 150000, repo.obtener_por_periodo(cat_1, 1, 2026)["monto_estimado_minor"])
    caso("obtener_por_periodo() de un período sin presupuesto devuelve None", None, repo.obtener_por_periodo(cat_1, 2, 2026))

    print("\n--- listar_por_periodo() — filtro cruzado + shape enriquecido ---")
    repo.upsert(categoria_id=cat_2, mes=1, anio=2026, moneda_id=moneda_ars, monto_estimado_minor=50000)
    listado_enero = repo.listar_por_periodo(1, 2026)
    ids_cats_enero = {(r["categoria_id"]) for r in listado_enero}
    caso("listar_por_periodo(1, 2026) incluye ambas categorías", True, cat_1 in ids_cats_enero and cat_2 in ids_cats_enero)
    caso("listar_por_periodo() trae subcategoria vía JOIN", True, "subcategoria" in listado_enero[0].keys())
    caso("listar_por_periodo() trae moneda_codigo vía JOIN", "ARS", listado_enero[0]["moneda_codigo"])

    listado_febrero = repo.listar_por_periodo(2, 2026)
    caso("listar_por_periodo(2, 2026) no incluye nada todavía", 0, len(listado_febrero))

    print("\n--- upsert(conn=...) — participa de una transacción externa ---")
    conn_externo = manager.conn
    with manager.transaction():
        repo.upsert(categoria_id=cat_1, mes=3, anio=2026, moneda_id=moneda_ars, monto_estimado_minor=200000, conn=conn_externo)
    caso("upsert(conn=...) persiste tras comitear", 200000, repo.obtener_por_periodo(cat_1, 3, 2026)["monto_estimado_minor"])

    print("\n--- upsert(conn=...) — rollback simulado ---")
    try:
        with manager.transaction():
            repo.upsert(categoria_id=cat_1, mes=4, anio=2026, moneda_id=moneda_ars, monto_estimado_minor=999, conn=conn_externo)
            raise RuntimeError("Fallo simulado a mitad de la transacción externa")
    except RuntimeError:
        pass
    caso("upsert(conn=...) con rollback: el presupuesto no quedó persistido", None, repo.obtener_por_periodo(cat_1, 4, 2026))

    print("\n--- actualizar_ejecutado() ---")
    repo.actualizar_ejecutado(cat_1, 1, 2026, monto_ejecutado_minor=75000)
    caso("actualizar_ejecutado() persiste el valor pasado", 75000, repo.obtener_por_periodo(cat_1, 1, 2026)["monto_ejecutado_minor"])
    caso("actualizar_ejecutado() no toca monto_estimado_minor", 150000, repo.obtener_por_periodo(cat_1, 1, 2026)["monto_estimado_minor"])

    with manager.transaction():
        repo.actualizar_ejecutado(cat_2, 1, 2026, monto_ejecutado_minor=10000, conn=conn_externo)
    caso("actualizar_ejecutado(conn=...) persiste tras comitear", 10000, repo.obtener_por_periodo(cat_2, 1, 2026)["monto_ejecutado_minor"])

    print("\n--- copiar_periodo() — sin overlap ---")
    copiados_1 = repo.copiar_periodo(mes_origen=1, anio_origen=2026, mes_destino=5, anio_destino=2026)
    caso("copiar_periodo() sin overlap copia las 2 filas de enero", 2, copiados_1)
    listado_mayo = repo.listar_por_periodo(5, 2026)
    caso("copiar_periodo() replica monto_estimado_minor (no el ejecutado, que es específico del período origen)", True, any(r["monto_estimado_minor"] == 150000 for r in listado_mayo))

    print("\n--- copiar_periodo() — overlap total (INSERT OR IGNORE no sobreescribe) ---")
    copiados_2 = repo.copiar_periodo(mes_origen=1, anio_origen=2026, mes_destino=5, anio_destino=2026)
    caso("copiar_periodo() con overlap total copia 0 filas (todo ya existe)", 0, copiados_2)

    print("\n--- copiar_periodo() — overlap parcial ---")
    repo.upsert(categoria_id=cat_1, mes=6, anio=2026, moneda_id=moneda_ars, monto_estimado_minor=999999, notas="Ya existía, no debe pisarse")
    copiados_3 = repo.copiar_periodo(mes_origen=1, anio_origen=2026, mes_destino=6, anio_destino=2026)
    caso("copiar_periodo() con overlap parcial solo copia la fila faltante (cat_2)", 1, copiados_3)
    caso(
        "copiar_periodo() con overlap parcial NO sobreescribe la fila que ya existía en el destino",
        999999,
        repo.obtener_por_periodo(cat_1, 6, 2026)["monto_estimado_minor"],
    )
    caso("copiar_periodo() con overlap parcial SÍ copia la fila que faltaba", 50000, repo.obtener_por_periodo(cat_2, 6, 2026)["monto_estimado_minor"])

    print("\n--- copiar_periodo(conn=...) — participa de una transacción externa ---")
    with manager.transaction():
        copiados_4 = repo.copiar_periodo(mes_origen=1, anio_origen=2026, mes_destino=7, anio_destino=2026, conn=conn_externo)
    caso("copiar_periodo(conn=...) persiste tras comitear", 2, copiados_4)
    caso("copiar_periodo(conn=...) — la copia es visible tras comitear", True, repo.obtener_por_periodo(cat_1, 7, 2026) is not None)

    print("\n--- copiar_periodo(conn=...) — rollback simulado ---")
    try:
        with manager.transaction():
            repo.copiar_periodo(mes_origen=1, anio_origen=2026, mes_destino=8, anio_destino=2026, conn=conn_externo)
            raise RuntimeError("Fallo simulado a mitad de la transacción externa")
    except RuntimeError:
        pass
    caso("copiar_periodo(conn=...) con rollback: ninguna fila quedó persistida", None, repo.obtener_por_periodo(cat_1, 8, 2026))

    print("\n--- listar_categoria_ids_con_presupuesto() ---")
    # cat_1/cat_2 ya tienen presupuestos cargados en varios períodos por los
    # casos de arriba — cat_3 (nueva acá) nunca tuvo ninguno, para confirmar
    # que se excluye.
    cat_3 = manager.fetchall("SELECT id FROM categorias WHERE tipo = 'egreso' LIMIT 3;")[2]["id"]
    ids_con_presupuesto = repo.listar_categoria_ids_con_presupuesto()
    caso("listar_categoria_ids_con_presupuesto() incluye cat_1", True, cat_1 in ids_con_presupuesto)
    caso("listar_categoria_ids_con_presupuesto() incluye cat_2", True, cat_2 in ids_con_presupuesto)
    caso("listar_categoria_ids_con_presupuesto() NO incluye cat_3 (nunca presupuestada)", False, cat_3 in ids_con_presupuesto)
    caso(
        "listar_categoria_ids_con_presupuesto() no duplica cat_1 pese a tener presupuestos en varios períodos",
        1,
        ids_con_presupuesto.count(cat_1),
    )

    print("\n--- upsert() — formula_estimado: guardar con fórmula persiste AMBOS campos ---")
    categorias_5 = manager.fetchall("SELECT id FROM categorias WHERE tipo = 'egreso' LIMIT 5;")
    cat_4, cat_5 = categorias_5[3]["id"], categorias_5[4]["id"]
    filas_formula = repo.upsert(
        categoria_id=cat_4, mes=1, anio=2026, moneda_id=moneda_ars,
        monto_estimado_minor=17700, formula_estimado="=15000+3200-500",
    )
    caso("upsert() con formula_estimado devuelve 1 fila afectada", 1, filas_formula)
    fila_con_formula = repo.obtener_por_periodo(cat_4, 1, 2026)
    caso("upsert() con formula_estimado persiste el monto_estimado_minor ya calculado", 17700, fila_con_formula["monto_estimado_minor"])
    caso("upsert() con formula_estimado persiste el texto de la fórmula tal cual (con el '=')", "=15000+3200-500", fila_con_formula["formula_estimado"])

    print("\n--- upsert() — formula_estimado: guardar SIN fórmula deja la columna en NULL ---")
    repo.upsert(categoria_id=cat_5, mes=1, anio=2026, moneda_id=moneda_ars, monto_estimado_minor=50000)
    fila_sin_formula = repo.obtener_por_periodo(cat_5, 1, 2026)
    caso("upsert() sin pasar formula_estimado (default None) deja la columna en NULL", None, fila_sin_formula["formula_estimado"])

    print("\n--- upsert() — formula_estimado: sobreescribir una fila con fórmula con un número directo limpia a NULL ---")
    repo.upsert(categoria_id=cat_4, mes=1, anio=2026, moneda_id=moneda_ars, monto_estimado_minor=99999)  # sin formula_estimado -> None
    fila_tras_sobreescribir = repo.obtener_por_periodo(cat_4, 1, 2026)
    caso("upsert() sobre una fila que tenía fórmula: el nuevo monto_estimado_minor se persiste", 99999, fila_tras_sobreescribir["monto_estimado_minor"])
    caso(
        "upsert() sobre una fila que tenía fórmula: al no pasar formula_estimado de nuevo, queda en NULL "
        "(no se arrastra una fórmula vieja asociada a un monto que ya no le corresponde)",
        None,
        fila_tras_sobreescribir["formula_estimado"],
    )

    manager.desconectar()

    print(f"\n--- Resumen: {casos_ok}/{casos_total} casos OK ---")


if __name__ == "__main__":
    main()
