"""
verify/compras_cuotas/verify_cuotas_credito_repository.py

Verifica CuotasCreditoRepository (repositories/cuotas_credito_repository.py):
crear_lote (standalone y con conn + rollback simulado), obtener_por_id,
listar_por_compra, listar_por_mes con filtros cruzados (incluye/excluye por
mes/año/estado), y las TRES transiciones de estado reales relevadas en
services/fees_service.py, cada una probada como la transición específica
que dispara su método real — no solo "cambia de estado":

    marcar_estado()              → pendiente → en_resumen  (confirm_fee)
    marcar_estado_por_resumen()  → en_resumen → pagado, en BLOQUE (pay_statement)
    marcar_estado_por_compra()   → pendiente → omitido, en BLOQUE (cancel_purchase)

Cada transición en bloque se prueba confirmando también que las cuotas que
NO deberían tocarse (otro resumen, otra compra, otro estado de partida)
efectivamente quedan intactas — igual que hace el código real (pay_statement
solo toca 'en_resumen' de ESE resumen; cancel_purchase solo toca
'pendiente' de ESA compra).

También prueba, para cada transición, la atomicidad real con su contraparte
de la otra tabla (resumenes_tarjeta o compras_cuotas) dentro de la misma
transacción externa, con un rollback simulado a mitad de camino.

Correlo con:
    python verify/compras_cuotas/verify_cuotas_credito_repository.py
"""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from verify._dummy_db import crear_dummy_db
from db.database import DatabaseManager
from repositories.cuentas_repository import CuentasRepository
from repositories.compras_cuotas_repository import ComprasCuotasRepository
from repositories.cuotas_credito_repository import CuotasCreditoRepository
from repositories.resumenes_tarjeta_repository import ResumenesTarjetaRepository


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

    def snapshot_estados() -> dict:
        """Captura {id: estado} de TODAS las filas de cuotas_credito, sin excepción."""
        filas = manager.fetchall("SELECT id, estado FROM cuotas_credito;")
        return {fila["id"]: fila["estado"] for fila in filas}

    def verificar_scope_exacto(
        descripcion_base: str,
        snapshot_antes: dict,
        snapshot_despues: dict,
        ids_esperados: set,
        estado_esperado_nuevo: str,
    ) -> None:
        """
        Compara dos snapshots COMPLETOS de la tabla (no solo las filas que se
        esperaba tocar) y confirma que el conjunto de ids que efectivamente
        cambió de estado es EXACTAMENTE ids_esperados, ni una fila de más ni
        de menos — así se detecta un WHERE demasiado amplio (falla tipo b)
        aunque no se le haya ocurrido a quien escribe el test nombrar esa
        fila de antemano.
        """
        ids_que_cambiaron = {
            id_ for id_, estado in snapshot_despues.items()
            if snapshot_antes.get(id_) != estado
        }
        caso(
            f"{descripcion_base}: el conjunto EXACTO de filas que cambiaron de estado en TODA la tabla es el esperado",
            ids_esperados,
            ids_que_cambiaron,
        )
        caso(
            f"{descripcion_base}: todas las filas que cambiaron quedaron en el estado nuevo esperado ('{estado_esperado_nuevo}')",
            True,
            all(snapshot_despues[id_] == estado_esperado_nuevo for id_ in ids_que_cambiaron),
        )

    db_path = crear_dummy_db()
    print(f"Dummy DB creada en: {db_path}\n")

    manager = DatabaseManager(db_path=db_path)
    manager.inicializar()
    cuentas_repo = CuentasRepository(manager)
    compras_repo = ComprasCuotasRepository(manager)
    repo = CuotasCreditoRepository(manager)
    resumenes_repo = ResumenesTarjetaRepository(manager)

    cuenta_a = cuentas_repo.crear(nombre="Tarjeta Cuotas", tipo="credito", moneda_codigo="ARS")
    moneda_ars = manager.fetchone("SELECT id FROM monedas WHERE codigo = 'ARS';")["id"]
    cat_egreso = manager.fetchone("SELECT id FROM categorias WHERE tipo = 'egreso' LIMIT 1;")["id"]

    compra_x = compras_repo.crear(
        fecha_compra="2026-01-05", concepto="Compra X", cuenta_id=cuenta_a,
        categoria_id=cat_egreso, moneda_id=moneda_ars,
        monto_total_minor=400000, total_cuotas=4, monto_por_cuota_minor=100000,
    )
    compra_y = compras_repo.crear(
        fecha_compra="2026-01-08", concepto="Compra Y", cuenta_id=cuenta_a,
        categoria_id=cat_egreso, moneda_id=moneda_ars,
        monto_total_minor=100000, total_cuotas=2, monto_por_cuota_minor=50000,
    )

    print("--- crear_lote() — standalone ---")
    ids_x = repo.crear_lote(
        compra_id=compra_x,
        cuotas=[
            {"numero_cuota": 1, "mes_proyectado": 1, "anio_proyectado": 2026, "monto_cuota_minor": 100000},
            {"numero_cuota": 2, "mes_proyectado": 2, "anio_proyectado": 2026, "monto_cuota_minor": 100000},
            {"numero_cuota": 3, "mes_proyectado": 3, "anio_proyectado": 2026, "monto_cuota_minor": 100000},
            {"numero_cuota": 4, "mes_proyectado": 4, "anio_proyectado": 2026, "monto_cuota_minor": 100000},
        ],
    )
    ids_y = repo.crear_lote(
        compra_id=compra_y,
        cuotas=[
            {"numero_cuota": 1, "mes_proyectado": 1, "anio_proyectado": 2026, "monto_cuota_minor": 50000},
            {"numero_cuota": 2, "mes_proyectado": 2, "anio_proyectado": 2026, "monto_cuota_minor": 50000},
        ],
    )
    caso("crear_lote() de X devuelve 4 ids", 4, len(ids_x))
    caso("crear_lote() de Y devuelve 2 ids", 2, len(ids_y))
    fee_x1, fee_x2, fee_x3, fee_x4 = ids_x
    fee_y1, fee_y2 = ids_y

    fila_x1_cruda = manager.fetchone("SELECT * FROM cuotas_credito WHERE id = ?;", (fee_x1,))
    caso("crear_lote() deja estado='pendiente' por default de columna", "pendiente", fila_x1_cruda["estado"])
    caso("crear_lote() persiste monto_cuota_minor correcto", 100000, fila_x1_cruda["monto_cuota_minor"])

    print("\n--- obtener_por_id() ---")
    caso("obtener_por_id() encuentra la cuota 1 de X", 1, repo.obtener_por_id(fee_x1)["numero_cuota"])
    caso("obtener_por_id() de un id inexistente devuelve None", None, repo.obtener_por_id(999999))

    print("\n--- listar_por_compra() ---")
    cuotas_x = repo.listar_por_compra(compra_x)
    caso("listar_por_compra(X) devuelve las 4 cuotas de X", 4, len(cuotas_x))
    caso("listar_por_compra(X) está ordenado por numero_cuota ASC", [1, 2, 3, 4], [c["numero_cuota"] for c in cuotas_x])
    caso("listar_por_compra(X) no incluye cuotas de Y", True, all(c["compra_id"] == compra_x for c in cuotas_x))

    print("\n--- listar_por_mes() — filtro cruzado por mes/año ---")
    mes_1 = repo.listar_por_mes(1, 2026)
    ids_mes_1 = [c["id"] for c in mes_1]
    caso("listar_por_mes(1, 2026) incluye la cuota 1 de X", True, fee_x1 in ids_mes_1)
    caso("listar_por_mes(1, 2026) incluye la cuota 1 de Y (mismo mes, compra distinta)", True, fee_y1 in ids_mes_1)
    caso("listar_por_mes(1, 2026) excluye la cuota 4 de X (abril)", False, fee_x4 in ids_mes_1)

    mes_4 = repo.listar_por_mes(4, 2026)
    ids_mes_4 = [c["id"] for c in mes_4]
    caso("listar_por_mes(4, 2026) incluye solo la cuota 4 de X", [fee_x4], ids_mes_4)

    print("\n--- crear_lote(conn=...) — rollback simulado a mitad de camino ---")
    compra_z = compras_repo.crear(
        fecha_compra="2026-05-01", concepto="Compra Z (va a fallar)", cuenta_id=cuenta_a,
        categoria_id=cat_egreso, moneda_id=moneda_ars,
        monto_total_minor=200000, total_cuotas=2, monto_por_cuota_minor=100000,
    )
    conn_externo = manager.conn
    cuotas_antes = manager.fetchone("SELECT COUNT(*) AS n FROM cuotas_credito;")["n"]
    try:
        with manager.transaction():
            repo.crear_lote(
                compra_id=compra_z,
                cuotas=[
                    {"numero_cuota": 1, "mes_proyectado": 5, "anio_proyectado": 2026, "monto_cuota_minor": 100000},
                    {"numero_cuota": 2, "mes_proyectado": 6, "anio_proyectado": 2026, "monto_cuota_minor": 100000},
                ],
                conn=conn_externo,
            )
            raise RuntimeError("Fallo simulado a mitad de la transacción externa")
    except RuntimeError:
        pass
    cuotas_despues = manager.fetchone("SELECT COUNT(*) AS n FROM cuotas_credito;")["n"]
    caso("rollback revierte todo el lote de crear_lote(conn=...) (ninguna cuota huérfana)", cuotas_antes, cuotas_despues)

    print("\n--- marcar_estado() — transición pendiente → en_resumen (confirm_fee) ---")
    resumen_1 = resumenes_repo.crear(cuenta_id=cuenta_a, mes=1, anio=2026, porcentaje_impuesto_bp=0)
    repo.marcar_estado(
        fee_x1, "en_resumen", resumen_id=resumen_1,
        mes_real_pago=1, anio_real_pago=2026, notas="Apareció en resumen de enero",
    )
    fee_x1_actualizada = repo.obtener_por_id(fee_x1)
    caso("marcar_estado() deja estado='en_resumen'", "en_resumen", fee_x1_actualizada["estado"])
    caso("marcar_estado() setea resumen_id", resumen_1, fee_x1_actualizada["resumen_id"])
    caso("marcar_estado() setea mes_real_pago", 1, fee_x1_actualizada["mes_real_pago"])
    caso("marcar_estado() setea anio_real_pago", 2026, fee_x1_actualizada["anio_real_pago"])
    caso("marcar_estado() setea notas", "Apareció en resumen de enero", fee_x1_actualizada["notas"])

    # También fee_x2 al mismo resumen, para tener 2 cuotas 'en_resumen' en resumen_1
    # y poder probar marcar_estado_por_resumen() en bloque más abajo.
    repo.marcar_estado(fee_x2, "en_resumen", resumen_id=resumen_1, mes_real_pago=1, anio_real_pago=2026)
    caso("marcar_estado() de fee_x2 también queda 'en_resumen'", "en_resumen", repo.obtener_por_id(fee_x2)["estado"])
    caso("fee_x3 sigue 'pendiente' (marcar_estado no afecta otras cuotas)", "pendiente", repo.obtener_por_id(fee_x3)["estado"])

    print("\n--- marcar_estado() con conn=... + rollback junto a actualizar_totales() (par atómico real de confirm_fee) ---")
    monto_consumos_antes = resumenes_repo.obtener_por_id(resumen_1)["monto_consumos_minor"]
    estado_fee_x3_antes = repo.obtener_por_id(fee_x3)["estado"]
    try:
        with manager.transaction():
            repo.marcar_estado(
                fee_x3, "en_resumen", resumen_id=resumen_1,
                mes_real_pago=1, anio_real_pago=2026, conn=conn_externo,
            )
            resumenes_repo.actualizar_totales(
                resumen_1, monto_consumos_minor=999999, monto_total_pagado_minor=999999,
                conn=conn_externo,
            )
            raise RuntimeError("Fallo simulado a mitad de la transacción externa")
    except RuntimeError:
        pass
    caso(
        "rollback revierte marcar_estado() de fee_x3 (sigue en su estado previo)",
        estado_fee_x3_antes,
        repo.obtener_por_id(fee_x3)["estado"],
    )
    caso(
        "rollback revierte actualizar_totales() del resumen (monto_consumos_minor sin cambios)",
        monto_consumos_antes,
        resumenes_repo.obtener_por_id(resumen_1)["monto_consumos_minor"],
    )

    print("\n--- marcar_estado_por_resumen() — transición en BLOQUE en_resumen → pagado (pay_statement) ---")
    snapshot_antes_resumen = snapshot_estados()
    afectadas = repo.marcar_estado_por_resumen(resumen_1, "en_resumen", "pagado")
    snapshot_despues_resumen = snapshot_estados()
    caso("marcar_estado_por_resumen() afecta exactamente 2 cuotas (fee_x1 y fee_x2)", 2, afectadas)
    caso("fee_x1 queda 'pagado'", "pagado", repo.obtener_por_id(fee_x1)["estado"])
    caso("fee_x2 queda 'pagado'", "pagado", repo.obtener_por_id(fee_x2)["estado"])
    caso("fee_x3 (su marcar_estado se revirtió por el rollback anterior — nunca quedó en_resumen) no fue tocada", "pendiente", repo.obtener_por_id(fee_x3)["estado"])
    caso("fee_x4 (otro resumen/ninguno) no fue tocada", "pendiente", repo.obtener_por_id(fee_x4)["estado"])
    filas_cambiadas_resumen = sum(1 for id_, e in snapshot_despues_resumen.items() if snapshot_antes_resumen.get(id_) != e)
    caso("el conteo retornado coincide exactamente con la cantidad real de filas cambiadas en TODA la tabla", afectadas, filas_cambiadas_resumen)
    verificar_scope_exacto(
        "marcar_estado_por_resumen(resumen_1)",
        snapshot_antes_resumen, snapshot_despues_resumen,
        {fee_x1, fee_x2}, "pagado",
    )

    print("\n--- marcar_estado_por_resumen(conn=...) + rollback junto a marcar_pagado() (par atómico real de pay_statement) ---")
    resumen_2 = resumenes_repo.crear(cuenta_id=cuenta_a, mes=2, anio=2026)
    repo.marcar_estado(fee_y1, "en_resumen", resumen_id=resumen_2, mes_real_pago=2, anio_real_pago=2026)
    estado_resumen_2_antes = resumenes_repo.obtener_por_id(resumen_2)["estado"]
    estado_fee_y1_antes = repo.obtener_por_id(fee_y1)["estado"]
    snapshot_antes_rollback_resumen = snapshot_estados()
    try:
        with manager.transaction():
            repo.marcar_estado_por_resumen(resumen_2, "en_resumen", "pagado", conn=conn_externo)
            resumenes_repo.marcar_pagado(resumen_2, fecha_pago="2026-02-15", conn=conn_externo)
            raise RuntimeError("Fallo simulado a mitad de la transacción externa")
    except RuntimeError:
        pass
    caso(
        "rollback revierte marcar_estado_por_resumen() (fee_y1 sigue 'en_resumen')",
        estado_fee_y1_antes,
        repo.obtener_por_id(fee_y1)["estado"],
    )
    caso(
        "rollback revierte marcar_pagado() del resumen (sigue sin estado 'pagado')",
        estado_resumen_2_antes,
        resumenes_repo.obtener_por_id(resumen_2)["estado"],
    )
    caso(
        "rollback: NINGUNA fila de cuotas_credito en TODA la tabla quedó con estado distinto al previo",
        snapshot_antes_rollback_resumen,
        snapshot_estados(),
    )

    print("\n--- marcar_estado_por_compra() — transición en BLOQUE pendiente → omitido (cancel_purchase) ---")
    # fee_y1 ya está 'en_resumen' (no debe tocarse); fee_y2 sigue 'pendiente' (sí debe tocarse).
    snapshot_antes_compra = snapshot_estados()
    afectadas_y = repo.marcar_estado_por_compra(compra_y, "pendiente", "omitido", notas="Compra Y cancelada")
    snapshot_despues_compra = snapshot_estados()
    caso("marcar_estado_por_compra() afecta exactamente 1 cuota (solo fee_y2, pendiente)", 1, afectadas_y)
    caso("fee_y2 queda 'omitido'", "omitido", repo.obtener_por_id(fee_y2)["estado"])
    caso("fee_y2 recibe la notas del cancelado", "Compra Y cancelada", repo.obtener_por_id(fee_y2)["notas"])
    caso("fee_y1 (ya 'en_resumen') NO fue tocada por el cancelado", "en_resumen", repo.obtener_por_id(fee_y1)["estado"])
    filas_cambiadas_compra = sum(1 for id_, e in snapshot_despues_compra.items() if snapshot_antes_compra.get(id_) != e)
    caso("el conteo retornado coincide exactamente con la cantidad real de filas cambiadas en TODA la tabla", afectadas_y, filas_cambiadas_compra)
    verificar_scope_exacto(
        "marcar_estado_por_compra(compra_y)",
        snapshot_antes_compra, snapshot_despues_compra,
        {fee_y2}, "omitido",
    )

    print("\n--- marcar_estado_por_compra(conn=...) + rollback junto a cancelar() (par atómico real de cancel_purchase) ---")
    compra_w = compras_repo.crear(
        fecha_compra="2026-06-01", concepto="Compra W (para cancelar)", cuenta_id=cuenta_a,
        categoria_id=cat_egreso, moneda_id=moneda_ars,
        monto_total_minor=200000, total_cuotas=2, monto_por_cuota_minor=100000,
    )
    ids_w = repo.crear_lote(
        compra_id=compra_w,
        cuotas=[
            {"numero_cuota": 1, "mes_proyectado": 6, "anio_proyectado": 2026, "monto_cuota_minor": 100000},
            {"numero_cuota": 2, "mes_proyectado": 7, "anio_proyectado": 2026, "monto_cuota_minor": 100000},
        ],
    )
    fee_w1, fee_w2 = ids_w
    estado_compra_w_antes = compras_repo.obtener_por_id(compra_w)["estado"]
    snapshot_antes_rollback_compra = snapshot_estados()
    try:
        with manager.transaction():
            repo.marcar_estado_por_compra(compra_w, "pendiente", "omitido", notas="cancelación de prueba", conn=conn_externo)
            compras_repo.cancelar(compra_w, notas="cancelación de prueba", conn=conn_externo)
            raise RuntimeError("Fallo simulado a mitad de la transacción externa")
    except RuntimeError:
        pass
    caso(
        "rollback revierte marcar_estado_por_compra() (fee_w1 sigue 'pendiente')",
        "pendiente",
        repo.obtener_por_id(fee_w1)["estado"],
    )
    caso(
        "rollback: NINGUNA fila de cuotas_credito en TODA la tabla quedó con estado distinto al previo",
        snapshot_antes_rollback_compra,
        snapshot_estados(),
    )
    caso(
        "rollback revierte cancelar() de la compra (sigue en su estado previo)",
        estado_compra_w_antes,
        compras_repo.obtener_por_id(compra_w)["estado"],
    )

    manager.desconectar()

    print(f"\n--- Resumen: {casos_ok}/{casos_total} casos OK ---")


if __name__ == "__main__":
    main()
