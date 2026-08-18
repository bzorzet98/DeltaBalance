"""
verify/compras_cuotas/verify_compras_cuotas_repository.py

Verifica ComprasCuotasRepository (repositories/compras_cuotas_repository.py):
crear, obtener_por_id/obtener_enriquecida, listar/listar_enriquecida
respetando cada filtro por separado (cruzado: incluye y excluye), actualizar
con el sentinel NO_CAMBIAR vs None explícito, cancelar, y — el caso que más
importa de este bloque — que crear() de la compra y crear_lote() de las
cuotas (CuotasCreditoRepository) participan de la misma transacción externa
de forma atómica: si algo falla a mitad de camino, ni la compra ni ninguna
cuota quedan persistidas.

También confirma la asimetría deliberada entre obtener_enriquecida() (trae
currency_symbol) y listar_enriquecida() (NO trae currency_symbol) — réplica
exacta de get_purchase()/list_purchases() en el service tal como están hoy.

Correlo con:
    python verify/compras_cuotas/verify_compras_cuotas_repository.py
"""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from verify._dummy_db import crear_dummy_db
from db.database import DatabaseManager
from repositories.cuentas_repository import CuentasRepository
from repositories.compras_cuotas_repository import ComprasCuotasRepository
from repositories.cuotas_credito_repository import CuotasCreditoRepository
from repositories._sentinels import NO_CAMBIAR


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
    cuentas_repo = CuentasRepository(manager)
    repo = ComprasCuotasRepository(manager)
    cuotas_repo = CuotasCreditoRepository(manager)

    cuenta_a = cuentas_repo.crear(nombre="Tarjeta A", tipo="credito", moneda_codigo="ARS")
    cuenta_b = cuentas_repo.crear(nombre="Tarjeta B", tipo="credito", moneda_codigo="ARS")
    moneda_ars = manager.fetchone("SELECT id FROM monedas WHERE codigo = 'ARS';")["id"]
    moneda_usd = manager.fetchone("SELECT id FROM monedas WHERE codigo = 'USD';")["id"]
    cat_egreso = manager.fetchone("SELECT id FROM categorias WHERE tipo = 'egreso' LIMIT 1;")["id"]

    print("--- crear() ---")
    compra_a = repo.crear(
        fecha_compra="2026-01-05",
        concepto="Lavarropas",
        cuenta_id=cuenta_a,
        categoria_id=cat_egreso,
        moneda_id=moneda_ars,
        monto_total_minor=1200000,
        total_cuotas=12,
        monto_por_cuota_minor=100000,
    )
    compra_b = repo.crear(
        fecha_compra="2026-02-10",
        concepto="Notebook",
        cuenta_id=cuenta_b,
        categoria_id=cat_egreso,
        moneda_id=moneda_usd,
        monto_total_minor=200000,
        total_cuotas=6,
        monto_por_cuota_minor=33334,
    )
    caso("crear() devuelve ids numéricos distintos para A y B", True, isinstance(compra_a, int) and isinstance(compra_b, int) and compra_a != compra_b)

    fila_a_cruda = manager.fetchone("SELECT * FROM compras_cuotas WHERE id = ?;", (compra_a,))
    caso("crear() deja estado='activa' por default de columna", "activa", fila_a_cruda["estado"])
    caso("crear() no setea modo_deuda explícito (queda el default 'prorrateado')", "prorrateado", fila_a_cruda["modo_deuda"])
    caso("crear() no setea monto_reintegro_minor explícito (queda el default 0)", 0, fila_a_cruda["monto_reintegro_minor"])

    print("\n--- obtener_por_id() ---")
    caso("obtener_por_id() encuentra la compra A", "Lavarropas", repo.obtener_por_id(compra_a)["concepto"])
    caso("obtener_por_id() de un id inexistente devuelve None", None, repo.obtener_por_id(999999))

    print("\n--- obtener_enriquecida() ---")
    fila_a_enriquecida = repo.obtener_enriquecida(compra_a)
    caso("obtener_enriquecida() trae account_name vía JOIN", "Tarjeta A", fila_a_enriquecida["account_name"])
    caso("obtener_enriquecida() trae category_name vía JOIN", True, fila_a_enriquecida["category_name"] is not None)
    caso("obtener_enriquecida() trae currency_code vía JOIN", "ARS", fila_a_enriquecida["currency_code"])
    caso("obtener_enriquecida() trae currency_symbol vía JOIN (sí lo trae, a diferencia de listar_enriquecida)", "$", fila_a_enriquecida["currency_symbol"])
    caso("obtener_enriquecida() trae decimales vía JOIN", 2, fila_a_enriquecida["decimales"])

    print("\n--- listar() — filtro por cuenta_id ---")
    solo_cuenta_a = repo.listar(cuenta_id=cuenta_a)
    ids_cuenta_a = [r["id"] for r in solo_cuenta_a]
    caso("filtro de cuenta_id incluye A", True, compra_a in ids_cuenta_a)
    caso("filtro de cuenta_id excluye B", False, compra_b in ids_cuenta_a)

    print("\n--- listar() — filtro por moneda_id ---")
    solo_usd = repo.listar(moneda_id=moneda_usd)
    ids_usd = [r["id"] for r in solo_usd]
    caso("filtro de moneda_id incluye B (USD)", True, compra_b in ids_usd)
    caso("filtro de moneda_id excluye A (ARS)", False, compra_a in ids_usd)

    print("\n--- listar() — filtro por estado ---")
    solo_activas = repo.listar(estado="activa")
    ids_activas = [r["id"] for r in solo_activas]
    caso("filtro de estado incluye A y B (ambas activas)", True, compra_a in ids_activas and compra_b in ids_activas)
    solo_canceladas = repo.listar(estado="cancelada")
    caso("filtro de estado='cancelada' no incluye nada todavía", False, compra_a in [r["id"] for r in solo_canceladas])

    print("\n--- listar_enriquecida() — misma asimetría que list_purchases() ---")
    listado_enriquecido = repo.listar_enriquecida(cuenta_id=cuenta_a)
    ids_listado = [r["id"] for r in listado_enriquecido]
    caso("listar_enriquecida(cuenta_id=...) incluye A", True, compra_a in ids_listado)
    fila_lista = next((r for r in listado_enriquecido if r["id"] == compra_a), None)
    caso("listar_enriquecida() trae account_name", True, "account_name" in fila_lista.keys())
    caso("listar_enriquecida() trae currency_code", True, "currency_code" in fila_lista.keys())
    caso("listar_enriquecida() NO trae currency_symbol (asimetría real de list_purchases())", False, "currency_symbol" in fila_lista.keys())

    print("\n--- actualizar() — sentinel NO_CAMBIAR vs None explícito ---")
    repo.actualizar(compra_b, notas="nota inicial")
    fila_b_1 = repo.obtener_por_id(compra_b)
    caso("actualizar() aplica notas cuando se pasan", "nota inicial", fila_b_1["notas"])

    repo.actualizar(compra_b, concepto="Notebook actualizada")
    fila_b_2 = repo.obtener_por_id(compra_b)
    caso("actualizar(concepto=...) solo: concepto cambia", "Notebook actualizada", fila_b_2["concepto"])
    caso("actualizar(concepto=...) solo: notas mantiene su valor previo (NO_CAMBIAR, no NULL)", "nota inicial", fila_b_2["notas"])

    repo.actualizar(compra_b, notas=None, estado=NO_CAMBIAR)
    fila_b_3 = repo.obtener_por_id(compra_b)
    caso("actualizar(notas=None) escribe NULL explícito", None, fila_b_3["notas"])
    caso("actualizar(notas=None) no toca concepto", "Notebook actualizada", fila_b_3["concepto"])
    caso("actualizar(estado=NO_CAMBIAR explícito) no toca estado", "activa", fila_b_3["estado"])

    sin_cambios = repo.actualizar(compra_b)
    caso("actualizar() sin ningún campo devuelve False", False, sin_cambios)

    print("\n--- cancelar() ---")
    repo.cancelar(compra_b, notas="Cliente se arrepintió")
    fila_b_cancelada = repo.obtener_por_id(compra_b)
    caso("cancelar() deja estado='cancelada'", "cancelada", fila_b_cancelada["estado"])
    caso("cancelar() escribe notas", "Cliente se arrepintió", fila_b_cancelada["notas"])

    print("\n--- crear() + CuotasCreditoRepository.crear_lote() — atomicidad exitosa ---")
    conn_externo = manager.conn
    with manager.transaction():
        compra_c = repo.crear(
            fecha_compra="2026-03-01",
            concepto="Heladera",
            cuenta_id=cuenta_a,
            categoria_id=cat_egreso,
            moneda_id=moneda_ars,
            monto_total_minor=300000,
            total_cuotas=3,
            monto_por_cuota_minor=100000,
            conn=conn_externo,
        )
        cuotas_ids = cuotas_repo.crear_lote(
            compra_id=compra_c,
            cuotas=[
                {"numero_cuota": 1, "mes_proyectado": 3, "anio_proyectado": 2026, "monto_cuota_minor": 100000},
                {"numero_cuota": 2, "mes_proyectado": 4, "anio_proyectado": 2026, "monto_cuota_minor": 100000},
                {"numero_cuota": 3, "mes_proyectado": 5, "anio_proyectado": 2026, "monto_cuota_minor": 100000},
            ],
            conn=conn_externo,
        )
    caso("crear() con conn en transacción exitosa: la compra persiste", "Heladera", repo.obtener_por_id(compra_c)["concepto"])
    caso("crear_lote() con conn en transacción exitosa: las 3 cuotas persisten", 3, len(cuotas_repo.listar_por_compra(compra_c)))
    caso("crear_lote() devuelve 3 ids", 3, len(cuotas_ids))

    print("\n--- crear() + crear_lote() — rollback simulado a mitad de camino ---")
    compras_antes = manager.fetchone("SELECT COUNT(*) AS n FROM compras_cuotas;")["n"]
    cuotas_antes = manager.fetchone("SELECT COUNT(*) AS n FROM cuotas_credito;")["n"]
    try:
        with manager.transaction():
            compra_d = repo.crear(
                fecha_compra="2026-04-01",
                concepto="Compra que va a fallar",
                cuenta_id=cuenta_a,
                categoria_id=cat_egreso,
                moneda_id=moneda_ars,
                monto_total_minor=500000,
                total_cuotas=5,
                monto_por_cuota_minor=100000,
                conn=conn_externo,
            )
            cuotas_repo.crear_lote(
                compra_id=compra_d,
                cuotas=[
                    {"numero_cuota": 1, "mes_proyectado": 4, "anio_proyectado": 2026, "monto_cuota_minor": 100000},
                    {"numero_cuota": 2, "mes_proyectado": 5, "anio_proyectado": 2026, "monto_cuota_minor": 100000},
                ],
                conn=conn_externo,
            )
            raise RuntimeError("Fallo simulado a mitad de la transacción externa")
    except RuntimeError:
        pass
    compras_despues = manager.fetchone("SELECT COUNT(*) AS n FROM compras_cuotas;")["n"]
    cuotas_despues = manager.fetchone("SELECT COUNT(*) AS n FROM cuotas_credito;")["n"]
    caso("rollback revierte el INSERT de la compra (no queda huérfana)", compras_antes, compras_despues)
    caso("rollback revierte también las N cuotas del lote (ninguna queda huérfana)", cuotas_antes, cuotas_despues)

    manager.desconectar()

    print(f"\n--- Resumen: {casos_ok}/{casos_total} casos OK ---")


if __name__ == "__main__":
    main()
