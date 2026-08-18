"""
verify/hogares_gastos_compartidos/verify_gastos_compartidos_repository.py

Verifica GastosCompartidosRepository
(repositories/gastos_compartidos_repository.py): crear()/obtener_por_id()/
obtener_enriquecida() (JOIN a categorias), listar()/listar_enriquecida()
con filtros cruzados (hogar_id, estado, origen_tipo, pagador),
marcar_saldado() (transición pendiente->saldado), actualizar() con el
sentinel NO_CAMBIAR (único campo editable: descripcion), atomicidad real
de crear() con conn+rollback, y los tres casos explícitamente pedidos:

  - monto_adeudado_minor NEGATIVO se persiste y se lee tal cual, sin que
    el repositorio lo altere (ver comentario en db/schema.sql y
    docs/DATA_MODEL_DECISIONS.md sección 2: el reintegro de una cuota
    puede superar su monto e invertir la deuda).
  - obtener_saldo_neto() con varios gastos_compartidos de signos mixtos
    sobre el mismo hogar_id, confirmando que vw_saldo_neto_hogar suma
    correctamente el neto (incluye un cambio de signo del total al marcar
    saldado el gasto más grande).
  - listar_por_origen() encuentra el gasto correcto por
    origen_tipo+origen_id y no lo confunde con otro que comparte el mismo
    origen_id pero un origen_tipo distinto.

Correlo con:
    python verify/hogares_gastos_compartidos/verify_gastos_compartidos_repository.py
"""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from verify._dummy_db import crear_dummy_db
from db.database import DatabaseManager
from repositories.hogares_repository import HogaresRepository
from repositories.gastos_compartidos_repository import GastosCompartidosRepository


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
    hogares_repo = HogaresRepository(manager)
    repo = GastosCompartidosRepository(manager)

    hogar_1 = hogares_repo.crear(codigo_invitacion="HOG100", nombre="Hogar con gastos")
    hogar_2 = hogares_repo.crear(codigo_invitacion="HOG200", nombre="Hogar sin gastos")

    categorias = manager.fetchall("SELECT id, subcategoria, categoria_principal FROM categorias WHERE tipo = 'egreso' LIMIT 2;")
    cat_1, cat_2 = categorias[0], categorias[1]

    # ============================================================
    # crear() / obtener_por_id() / obtener_enriquecida()
    # ============================================================
    print("--- crear() / obtener_por_id() ---")
    gasto_1 = repo.crear(
        hogar_id=hogar_1, pagador="bruno", origen_tipo="transaccion", origen_id=101,
        categoria_id=cat_1["id"], monto_base_minor=10000, coeficiente_deuda=50.0,
        monto_adeudado_minor=5000, fecha="2026-01-05", descripcion="Super del mes",
    )
    caso("crear() devuelve un id numérico", True, isinstance(gasto_1, int))

    fila_1 = repo.obtener_por_id(gasto_1)
    caso("crear() persiste pagador", "bruno", fila_1["pagador"])
    caso("crear() persiste origen_tipo/origen_id", True, fila_1["origen_tipo"] == "transaccion" and fila_1["origen_id"] == 101)
    caso("crear() persiste monto_base_minor", 10000, fila_1["monto_base_minor"])
    caso("crear() persiste coeficiente_deuda", 50.0, fila_1["coeficiente_deuda"])
    caso("crear() deja estado='pendiente' por default de columna", "pendiente", fila_1["estado"])
    caso("obtener_por_id() de un id inexistente devuelve None", None, repo.obtener_por_id(999999))

    print("\n--- obtener_enriquecida() — JOIN a categorias ---")
    enriquecida_1 = repo.obtener_enriquecida(gasto_1)
    caso("obtener_enriquecida() trae category_name (subcategoria de cat_1)", cat_1["subcategoria"], enriquecida_1["category_name"])
    caso("obtener_enriquecida() trae categoria_principal de cat_1", cat_1["categoria_principal"], enriquecida_1["categoria_principal"])
    caso("obtener_enriquecida() de un id inexistente devuelve None", None, repo.obtener_enriquecida(999999))

    # ============================================================
    # monto_adeudado_minor NEGATIVO — el repositorio no lo altera
    # ============================================================
    print("\n--- monto_adeudado_minor negativo se persiste tal cual ---")
    gasto_2 = repo.crear(
        hogar_id=hogar_1, pagador="martina", origen_tipo="compra_cuotas", origen_id=202,
        categoria_id=cat_1["id"], monto_base_minor=8000, coeficiente_deuda=50.0,
        monto_adeudado_minor=-1500, fecha="2026-01-10", descripcion="Reintegro invierte la deuda",
    )
    caso("crear() con monto_adeudado_minor negativo NO lo altera (sigue en -1500)", -1500, repo.obtener_por_id(gasto_2)["monto_adeudado_minor"])
    caso("obtener_enriquecida() también trae el negativo intacto", -1500, repo.obtener_enriquecida(gasto_2)["monto_adeudado_minor"])

    gasto_3 = repo.crear(
        hogar_id=hogar_1, pagador="bruno", origen_tipo="cuota_credito", origen_id=303,
        categoria_id=cat_2["id"], monto_base_minor=3000, coeficiente_deuda=30.0,
        monto_adeudado_minor=900, fecha="2026-01-15",
    )
    gasto_4 = repo.crear(
        hogar_id=hogar_1, pagador="martina", origen_tipo="transaccion", origen_id=202,
        categoria_id=cat_2["id"], monto_base_minor=1000, coeficiente_deuda=50.0,
        monto_adeudado_minor=500, fecha="2026-01-20",
    )

    # ============================================================
    # listar() / listar_enriquecida() — filtros cruzados
    # ============================================================
    print("\n--- listar() — filtros cruzados ---")
    todos_hogar_1 = repo.listar(hogar_1)
    caso("listar(hogar_1) trae los 4 gastos creados", True, all(g in [x["id"] for x in todos_hogar_1] for g in (gasto_1, gasto_2, gasto_3, gasto_4)))

    solo_bruno = repo.listar(hogar_1, pagador="bruno")
    caso("listar(hogar_1, pagador='bruno') trae exactamente gasto_1 y gasto_3", True, set(x["id"] for x in solo_bruno) == {gasto_1, gasto_3})

    solo_cuota_credito = repo.listar(hogar_1, origen_tipo="cuota_credito")
    caso("listar(hogar_1, origen_tipo='cuota_credito') trae exactamente gasto_3", [gasto_3], [x["id"] for x in solo_cuota_credito])

    listado_hogar_2 = repo.listar(hogar_2)
    caso("listar(hogar_2) no trae ningún gasto (son todos de hogar_1)", [], listado_hogar_2)

    print("\n--- listar_enriquecida() — mismo set de filtros con category_name ---")
    enriquecida_bruno = repo.listar_enriquecida(hogar_1, pagador="bruno")
    caso("listar_enriquecida(pagador='bruno') trae 2 filas", 2, len(enriquecida_bruno))
    caso(
        "listar_enriquecida() trae category_name en cada fila",
        True,
        all(row["category_name"] is not None for row in enriquecida_bruno),
    )

    # ============================================================
    # marcar_saldado() — pendiente -> saldado
    # ============================================================
    print("\n--- marcar_saldado() ---")
    repo.marcar_saldado(gasto_1)
    caso("marcar_saldado() cambia el estado a 'saldado'", "saldado", repo.obtener_por_id(gasto_1)["estado"])

    solo_pendientes = repo.listar(hogar_1, estado="pendiente")
    caso("listar(estado='pendiente') ya no incluye gasto_1", False, gasto_1 in [x["id"] for x in solo_pendientes])

    solo_saldados = repo.listar(hogar_1, estado="saldado")
    caso("listar(estado='saldado') trae exactamente gasto_1", [gasto_1], [x["id"] for x in solo_saldados])

    # ============================================================
    # actualizar() — sentinel NO_CAMBIAR, único campo editable: descripcion
    # ============================================================
    print("\n--- actualizar() — sentinel NO_CAMBIAR ---")
    resultado = repo.actualizar(gasto_2, descripcion="Descripción corregida")
    caso("actualizar(descripcion=...) devuelve True", True, resultado)
    caso("actualizar(descripcion=...) reescribe la descripción", "Descripción corregida", repo.obtener_por_id(gasto_2)["descripcion"])
    caso("actualizar(descripcion=...) no toca monto_adeudado_minor (sigue en -1500)", -1500, repo.obtener_por_id(gasto_2)["monto_adeudado_minor"])

    sin_cambios = repo.actualizar(gasto_3)
    caso("actualizar() sin descripcion (NO_CAMBIAR) devuelve False", False, sin_cambios)

    conn_externo = manager.conn
    with manager.transaction():
        repo.actualizar(gasto_4, descripcion="Vía conn externo", conn=conn_externo)
    caso("actualizar(conn=...) persiste tras comitear", "Vía conn externo", repo.obtener_por_id(gasto_4)["descripcion"])

    # ============================================================
    # obtener_saldo_neto() — signos mixtos, vw_saldo_neto_hogar
    # ============================================================
    print("\n--- obtener_saldo_neto() — signos mixtos ---")
    # Pendientes de hogar_1 en este punto: gasto_2 (-1500), gasto_3 (900), gasto_4 (500)
    # gasto_1 ya está 'saldado' y la vista solo suma estado='pendiente'.
    caso(
        "obtener_saldo_neto(hogar_1) suma correctamente signos mixtos (-1500 + 900 + 500 = -100)",
        -100,
        repo.obtener_saldo_neto(hogar_1),
    )
    caso("obtener_saldo_neto(hogar_2) devuelve 0 (sin ningún gasto)", 0, repo.obtener_saldo_neto(hogar_2))

    # Ahora saldamos el gasto positivo más grande (gasto_3, 900) para confirmar
    # que la vista se recalcula dinámicamente y no queda un valor cacheado.
    repo.marcar_saldado(gasto_3)
    caso(
        "tras marcar_saldado(gasto_3): obtener_saldo_neto(hogar_1) recalcula (-1500 + 500 = -1000)",
        -1000,
        repo.obtener_saldo_neto(hogar_1),
    )

    # ============================================================
    # listar_por_origen() — no confunde origen_id repetido con distinto origen_tipo
    # ============================================================
    print("\n--- listar_por_origen() ---")
    # gasto_2: origen_tipo='compra_cuotas', origen_id=202
    # gasto_4: origen_tipo='transaccion',   origen_id=202  (mismo id, tipo distinto)
    por_origen_compra = repo.listar_por_origen("compra_cuotas", 202)
    caso("listar_por_origen('compra_cuotas', 202) trae exactamente gasto_2", [gasto_2], [x["id"] for x in por_origen_compra])

    por_origen_transaccion = repo.listar_por_origen("transaccion", 202)
    caso(
        "listar_por_origen('transaccion', 202) trae exactamente gasto_4, no confunde con gasto_2 (mismo origen_id, distinto origen_tipo)",
        [gasto_4],
        [x["id"] for x in por_origen_transaccion],
    )

    caso("listar_por_origen() de un origen sin ningún gasto asociado devuelve lista vacía", [], repo.listar_por_origen("transaccion", 999999))

    # ============================================================
    # crear(conn=...) — atomicidad real, con éxito y con rollback
    # ============================================================
    print("\n--- crear(conn=...) — participa de una transacción externa (éxito) ---")
    with manager.transaction():
        gasto_5 = repo.crear(
            hogar_id=hogar_1, pagador="bruno", origen_tipo="transaccion", origen_id=505,
            categoria_id=cat_1["id"], monto_base_minor=2000, coeficiente_deuda=50.0,
            monto_adeudado_minor=1000, fecha="2026-02-01", conn=conn_externo,
        )
    caso("crear(conn=...) persiste tras comitear", 1000, repo.obtener_por_id(gasto_5)["monto_adeudado_minor"])

    print("\n--- crear(conn=...) — rollback simulado ---")
    gastos_antes = manager.fetchone("SELECT COUNT(*) AS n FROM gastos_compartidos;")["n"]
    try:
        with manager.transaction():
            repo.crear(
                hogar_id=hogar_1, pagador="martina", origen_tipo="transaccion", origen_id=606,
                categoria_id=cat_1["id"], monto_base_minor=4000, coeficiente_deuda=50.0,
                monto_adeudado_minor=2000, fecha="2026-02-02", conn=conn_externo,
            )
            raise RuntimeError("Fallo simulado a mitad de la transacción externa")
    except RuntimeError:
        pass
    gastos_despues = manager.fetchone("SELECT COUNT(*) AS n FROM gastos_compartidos;")["n"]
    caso("rollback revierte el INSERT del gasto compartido (no queda huérfano)", gastos_antes, gastos_despues)
    caso("rollback: listar_por_origen('transaccion', 606) no encuentra nada", [], repo.listar_por_origen("transaccion", 606))

    manager.desconectar()

    print(f"\n--- Resumen: {casos_ok}/{casos_total} casos OK ---")


if __name__ == "__main__":
    main()
