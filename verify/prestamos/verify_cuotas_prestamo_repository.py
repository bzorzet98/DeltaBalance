"""
verify/prestamos/verify_cuotas_prestamo_repository.py

Verifica CuotasPrestamoRepository
(repositories/cuotas_prestamo_repository.py): crear_lote() standalone,
obtener_por_id(), listar_por_prestamo() confirmando EXPLÍCITAMENTE el
orden ASC por numero_cuota (insertando el lote en orden desordenado a
propósito, para probar que el ORDER BY realmente ordena y no es una
coincidencia del orden de inserción), listar_por_mes() con filtros
cruzados (incluye cuotas de DOS préstamos distintos en el mismo mes), y
marcar_pagada() con y sin conn.

Correlo con:
    python verify/prestamos/verify_cuotas_prestamo_repository.py
"""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from verify._dummy_db import crear_dummy_db
from db.database import DatabaseManager
from repositories.prestamos_repository import PrestamosRepository
from repositories.cuotas_prestamo_repository import CuotasPrestamoRepository


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
    prestamos_repo = PrestamosRepository(manager)
    repo = CuotasPrestamoRepository(manager)

    moneda_ars = manager.fetchone("SELECT id FROM monedas WHERE codigo = 'ARS';")["id"]

    prestamo_1 = prestamos_repo.crear(
        entidad="Test Bank", tipo="personal", capital_original_minor=400000,
        tasa_anual_bp=5000, sistema_amortizacion="frances", moneda_id=moneda_ars,
        fecha_inicio="2026-01-01", plazo_meses=4,
    )
    prestamo_2 = prestamos_repo.crear(
        entidad="Test Bank 2", tipo="prendario", capital_original_minor=300000,
        tasa_anual_bp=4200, sistema_amortizacion="aleman", moneda_id=moneda_ars,
        fecha_inicio="2026-01-01", plazo_meses=3,
    )

    # ============================================================
    # crear_lote() — insertado EN ORDEN DESORDENADO a propósito
    # ============================================================
    print("--- crear_lote() — lote insertado desordenado (numero_cuota: 3, 1, 4, 2) ---")
    ids_1 = repo.crear_lote(
        prestamo_id=prestamo_1,
        cuotas=[
            {"numero_cuota": 3, "monto_capital_minor": 98000, "monto_interes_minor": 2000, "monto_total_minor": 100000, "mes": 3, "anio": 2026},
            {"numero_cuota": 1, "monto_capital_minor": 95000, "monto_interes_minor": 5000, "monto_total_minor": 100000, "mes": 1, "anio": 2026},
            {"numero_cuota": 4, "monto_capital_minor": 98500, "monto_interes_minor": 1500, "monto_total_minor": 100000, "mes": 4, "anio": 2026},
            {"numero_cuota": 2, "monto_capital_minor": 96500, "monto_interes_minor": 3500, "monto_total_minor": 100000, "mes": 2, "anio": 2026},
        ],
    )
    caso("crear_lote() devuelve 4 ids distintos", 4, len(set(ids_1)))

    print("\n--- obtener_por_id() ---")
    primera_cuota_insertada = repo.obtener_por_id(ids_1[0])
    caso(
        "crear_lote() devuelve los ids en el MISMO orden que la lista de entrada: "
        "ids_1[0] corresponde a numero_cuota=3 (el primer dict del lote desordenado, no numero_cuota=1)",
        3,
        primera_cuota_insertada["numero_cuota"],
    )
    caso("obtener_por_id() de un id inexistente devuelve None", None, repo.obtener_por_id(999999))

    print("\n--- listar_por_prestamo() — confirma el orden ASC explícitamente ---")
    listado_1 = repo.listar_por_prestamo(prestamo_1)
    caso("listar_por_prestamo() trae las 4 cuotas", 4, len(listado_1))
    caso(
        "listar_por_prestamo() las devuelve ordenadas 1,2,3,4 por numero_cuota ASC — "
        "NO en el orden 3,1,4,2 en que se insertaron",
        [1, 2, 3, 4],
        [c["numero_cuota"] for c in listado_1],
    )

    # ============================================================
    # crear_lote() — segundo préstamo, secuencial, mismo mes que el primero
    # ============================================================
    print("\n--- crear_lote() — segundo préstamo (secuencial) ---")
    ids_2 = repo.crear_lote(
        prestamo_id=prestamo_2,
        cuotas=[
            {"numero_cuota": 1, "monto_capital_minor": 96000, "monto_interes_minor": 4000, "monto_total_minor": 100000, "mes": 1, "anio": 2026},
            {"numero_cuota": 2, "monto_capital_minor": 97000, "monto_interes_minor": 3000, "monto_total_minor": 100000, "mes": 2, "anio": 2026},
            {"numero_cuota": 3, "monto_capital_minor": 98000, "monto_interes_minor": 2000, "monto_total_minor": 100000, "mes": 3, "anio": 2026},
        ],
    )
    caso("crear_lote() del segundo préstamo devuelve 3 ids", 3, len(ids_2))

    # ============================================================
    # listar_por_mes() — filtros cruzados, dos préstamos en el mismo mes
    # ============================================================
    print("\n--- listar_por_mes() — filtros cruzados ---")
    mes_1_2026 = repo.listar_por_mes(1, 2026)
    caso(
        "listar_por_mes(1, 2026) trae la cuota #1 de AMBOS préstamos (2 filas)",
        2,
        len(mes_1_2026),
    )
    caso(
        "listar_por_mes(1, 2026) ordena por prestamo_id, numero_cuota ASC (prestamo_1 antes que prestamo_2)",
        [prestamo_1, prestamo_2],
        [c["prestamo_id"] for c in mes_1_2026],
    )

    mes_4_2026 = repo.listar_por_mes(4, 2026)
    caso("listar_por_mes(4, 2026) trae exactamente la cuota #4 de prestamo_1 (prestamo_2 no llega a ese mes)", 1, len(mes_4_2026))
    caso("listar_por_mes(4, 2026) trae el préstamo correcto", prestamo_1, mes_4_2026[0]["prestamo_id"])

    mes_sin_cuotas = repo.listar_por_mes(12, 2026)
    caso("listar_por_mes() de un mes sin ninguna cuota devuelve lista vacía", [], mes_sin_cuotas)

    todas_pendientes_mes_1 = repo.listar_por_mes(1, 2026, estado="pendiente")
    caso("listar_por_mes(1, 2026, estado='pendiente') trae las 2 (ninguna pagada todavía)", 2, len(todas_pendientes_mes_1))

    # ============================================================
    # marcar_pagada()
    # ============================================================
    print("\n--- marcar_pagada() ---")
    cuota_1_prestamo_1 = listado_1[0]["id"]  # numero_cuota=1 de prestamo_1, tras el orden ASC
    repo.marcar_pagada(cuota_1_prestamo_1, fecha_pago="2026-01-15")
    fila_pagada = repo.obtener_por_id(cuota_1_prestamo_1)
    caso("marcar_pagada() cambia el estado a 'pagado'", "pagado", fila_pagada["estado"])
    caso("marcar_pagada() persiste fecha_pago", "2026-01-15", fila_pagada["fecha_pago"])

    mes_1_2026_pendientes_tras_pago = repo.listar_por_mes(1, 2026, estado="pendiente")
    caso("listar_por_mes(1, 2026, estado='pendiente') tras el pago ya no incluye esa cuota (queda 1)", 1, len(mes_1_2026_pendientes_tras_pago))

    mes_1_2026_pagadas = repo.listar_por_mes(1, 2026, estado="pagado")
    caso("listar_por_mes(1, 2026, estado='pagado') trae exactamente la cuota pagada", [cuota_1_prestamo_1], [c["id"] for c in mes_1_2026_pagadas])

    print("\n--- marcar_pagada(conn=...) — participa de una transacción externa ---")
    cuota_2_prestamo_2 = ids_2[1]  # numero_cuota=2 de prestamo_2
    conn_externo = manager.conn
    with manager.transaction():
        repo.marcar_pagada(cuota_2_prestamo_2, fecha_pago="2026-02-10", conn=conn_externo)
    fila_pagada_conn = repo.obtener_por_id(cuota_2_prestamo_2)
    caso("marcar_pagada(conn=...) persiste tras comitear: estado='pagado'", "pagado", fila_pagada_conn["estado"])
    caso("marcar_pagada(conn=...) persiste tras comitear: fecha_pago", "2026-02-10", fila_pagada_conn["fecha_pago"])

    # ============================================================
    # actualizar() — sentinel NO_CAMBIAR (paso 1b)
    # ============================================================
    print("\n--- actualizar() — sentinel NO_CAMBIAR ---")
    cuota_3_prestamo_2 = ids_2[2]  # numero_cuota=3: capital=98000, interes=2000, total=100000
    fila_antes = repo.obtener_por_id(cuota_3_prestamo_2)
    caso("estado inicial de cuota_3_prestamo_2 antes de ajustar: monto_capital_minor=98000", 98000, fila_antes["monto_capital_minor"])
    caso("estado inicial de cuota_3_prestamo_2 antes de ajustar: monto_total_minor=100000", 100000, fila_antes["monto_total_minor"])

    repo.actualizar(cuota_3_prestamo_2, monto_interes_minor=2500)
    fila_solo_interes = repo.obtener_por_id(cuota_3_prestamo_2)
    caso("actualizar(monto_interes_minor=...) reescribe solo el interés", 2500, fila_solo_interes["monto_interes_minor"])
    caso("actualizar(monto_interes_minor=...) NO toca monto_capital_minor (NO_CAMBIAR)", 98000, fila_solo_interes["monto_capital_minor"])
    caso("actualizar(monto_interes_minor=...) NO toca monto_total_minor (NO_CAMBIAR)", 100000, fila_solo_interes["monto_total_minor"])

    repo.actualizar(cuota_3_prestamo_2, monto_capital_minor=97000, monto_interes_minor=3200, monto_total_minor=100200)
    fila_los_tres = repo.obtener_por_id(cuota_3_prestamo_2)
    caso(
        "actualizar() con los tres montos a la vez reescribe los tres",
        True,
        fila_los_tres["monto_capital_minor"] == 97000
        and fila_los_tres["monto_interes_minor"] == 3200
        and fila_los_tres["monto_total_minor"] == 100200,
    )

    sin_cambios_actualizar = repo.actualizar(cuota_3_prestamo_2)
    caso("actualizar() sin ningún campo devuelve False", False, sin_cambios_actualizar)

    with manager.transaction():
        repo.actualizar(cuota_3_prestamo_2, monto_interes_minor=3300, conn=conn_externo)
    caso("actualizar(conn=...) persiste tras comitear", 3300, repo.obtener_por_id(cuota_3_prestamo_2)["monto_interes_minor"])

    manager.desconectar()

    print(f"\n--- Resumen: {casos_ok}/{casos_total} casos OK ---")


if __name__ == "__main__":
    main()
