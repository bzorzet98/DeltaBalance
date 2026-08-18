"""
verify/compras_cuotas/verify_resumenes_tarjeta_repository.py

Verifica ResumenesTarjetaRepository (repositories/resumenes_tarjeta_repository.py):
crear, obtener_por_id/obtener_por_periodo/obtener_enriquecida, listar/
listar_enriquecida con filtros cruzados (incluye/excluye), actualizar_totales
(incluyendo que los campos opcionales que no se pasan quedan sin tocar),
marcar_cerrado y marcar_pagado.

También confirma el comportamiento nuevo de marcar_cerrado()/marcar_pagado()
del bloque CARGOS EXTRA DE RESUMEN (ver docs/DATA_MODEL_DECISIONS.md sección
12 y el docstring de ResumenesTarjetaRepository):
- marcar_cerrado() ahora calcula monto_consumos_minor sumando cuotas_credito
  reales del resumen, monto_impuestos_minor sumando resumen_cargos_extra
  reales (incluyendo un ajuste negativo) y porcentaje_impuesto_bp derivado
  de ambos — se confirma que los tres valores calculados coinciden con la
  aritmética esperada, tanto en el UPDATE persistido como en el dict que
  devuelve el método.
- marcar_pagado() ahora SÍ escribe monto_total_pagado_minor = el
  monto_pagado_minor pasado (antes no lo tocaba).

También confirma la corrección de un bug de diseño en marcar_cerrado():
las lecturas internas (suma de cuotas_credito, suma de resumen_cargos_extra)
ahora respetan de verdad el `conn` recibido, en vez de asumir que siempre
coincide con self._db.conn. El caso "conn GENUINAMENTE distinto a
self._db.conn" abre una SEGUNDA conexión sqlite3 real al mismo archivo,
inserta datos sin comitear en esa segunda conexión, y confirma que
marcar_cerrado(conn=esa_segunda_conexion) ve esos datos no comiteados —
si el método usara self._db.conn para las lecturas, este caso fallaría
(self._db.conn nunca ve cambios no comiteados de otra conexión), así que
prueba el comportamiento real en vez de asumirlo por coincidencia del
entorno de tests.

La atomicidad real de actualizar_totales()/marcar_pagado() con su
contraparte en CuotasCreditoRepository (los pares atómicos reales de
confirm_fee()/pay_statement()) ya se prueba con rollback simulado en
verify_cuotas_credito_repository.py — acá solo se confirma que ambos
métodos aceptan y usan `conn` correctamente en el caso exitoso, sin
duplicar esos escenarios de rollback.

Correlo con:
    python verify/compras_cuotas/verify_resumenes_tarjeta_repository.py
"""

from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from verify._dummy_db import crear_dummy_db
from db.database import DatabaseManager
from repositories.cuentas_repository import CuentasRepository
from repositories.resumenes_tarjeta_repository import ResumenesTarjetaRepository
from repositories.resumen_cargos_extra_repository import ResumenCargosExtraRepository
from repositories.compras_cuotas_repository import ComprasCuotasRepository
from repositories.cuotas_credito_repository import CuotasCreditoRepository


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
    repo = ResumenesTarjetaRepository(manager)
    cargos_repo = ResumenCargosExtraRepository(manager)
    compras_repo = ComprasCuotasRepository(manager)
    cuotas_repo = CuotasCreditoRepository(manager)

    cuenta_a = cuentas_repo.crear(nombre="Tarjeta Resumen A", tipo="credito", moneda_codigo="ARS")
    cuenta_b = cuentas_repo.crear(nombre="Tarjeta Resumen B", tipo="credito", moneda_codigo="ARS")
    moneda_ars = manager.fetchone("SELECT id FROM monedas WHERE codigo = 'ARS';")["id"]
    cat_egreso = manager.fetchone("SELECT id FROM categorias WHERE tipo = 'egreso' LIMIT 1;")["id"]

    print("--- crear() ---")
    resumen_1 = repo.crear(cuenta_id=cuenta_a, mes=1, anio=2026, porcentaje_impuesto_bp=500)
    resumen_2 = repo.crear(cuenta_id=cuenta_a, mes=2, anio=2026)
    resumen_3 = repo.crear(cuenta_id=cuenta_b, mes=1, anio=2026)
    caso(
        "crear() devuelve ids numéricos distintos",
        True,
        len({resumen_1, resumen_2, resumen_3}) == 3 and all(isinstance(x, int) for x in (resumen_1, resumen_2, resumen_3)),
    )

    fila_1_cruda = manager.fetchone("SELECT * FROM resumenes_tarjeta WHERE id = ?;", (resumen_1,))
    caso("crear() deja estado='abierto' por default de columna", "abierto", fila_1_cruda["estado"])
    caso("crear() arranca monto_consumos_minor en 0", 0, fila_1_cruda["monto_consumos_minor"])
    caso("crear() arranca monto_impuestos_minor en 0", 0, fila_1_cruda["monto_impuestos_minor"])
    caso("crear() arranca monto_total_pagado_minor en 0", 0, fila_1_cruda["monto_total_pagado_minor"])
    caso("crear() persiste porcentaje_impuesto_bp", 500, fila_1_cruda["porcentaje_impuesto_bp"])

    print("\n--- obtener_por_id() ---")
    caso("obtener_por_id() encuentra el resumen 1", 1, repo.obtener_por_id(resumen_1)["mes"])
    caso("obtener_por_id() de un id inexistente devuelve None", None, repo.obtener_por_id(999999))

    print("\n--- obtener_por_periodo() — filtro cruzado cuenta+mes+año ---")
    caso("obtener_por_periodo(cuenta_a, 1, 2026) encuentra resumen_1", resumen_1, repo.obtener_por_periodo(cuenta_a, 1, 2026)["id"])
    caso("obtener_por_periodo(cuenta_a, 3, 2026) no encuentra nada (no existe ese período)", None, repo.obtener_por_periodo(cuenta_a, 3, 2026))
    caso(
        "obtener_por_periodo(cuenta_b, 1, 2026) encuentra resumen_3, no resumen_1 (misma fecha, cuenta distinta)",
        resumen_3,
        repo.obtener_por_periodo(cuenta_b, 1, 2026)["id"],
    )

    print("\n--- obtener_enriquecida() ---")
    fila_1_enriquecida = repo.obtener_enriquecida(resumen_1)
    caso("obtener_enriquecida() trae account_name vía JOIN", "Tarjeta Resumen A", fila_1_enriquecida["account_name"])

    print("\n--- listar() — filtro por cuenta_id ---")
    solo_cuenta_a = repo.listar(cuenta_id=cuenta_a)
    ids_cuenta_a = [r["id"] for r in solo_cuenta_a]
    caso("filtro de cuenta_id incluye resumen_1 y resumen_2", True, resumen_1 in ids_cuenta_a and resumen_2 in ids_cuenta_a)
    caso("filtro de cuenta_id excluye resumen_3 (cuenta_b)", False, resumen_3 in ids_cuenta_a)

    print("\n--- listar() — filtro por anio ---")
    solo_2026 = repo.listar(anio=2026)
    caso("filtro de anio incluye los 3 resúmenes", True, all(x in [r["id"] for r in solo_2026] for x in (resumen_1, resumen_2, resumen_3)))
    solo_2027 = repo.listar(anio=2027)
    caso("filtro de anio=2027 no incluye nada", False, resumen_1 in [r["id"] for r in solo_2027])

    print("\n--- listar() — filtro por estado ---")
    solo_abiertos = repo.listar(estado="abierto")
    caso("filtro de estado incluye los 3 (todos abiertos todavía)", True, all(x in [r["id"] for r in solo_abiertos] for x in (resumen_1, resumen_2, resumen_3)))

    print("\n--- listar_enriquecida() ---")
    listado_enriquecido = repo.listar_enriquecida(cuenta_id=cuenta_a)
    ids_enriquecido = [r["id"] for r in listado_enriquecido]
    caso("listar_enriquecida(cuenta_id=...) incluye resumen_1 y resumen_2", True, resumen_1 in ids_enriquecido and resumen_2 in ids_enriquecido)
    caso("listar_enriquecida(cuenta_id=...) excluye resumen_3", False, resumen_3 in ids_enriquecido)
    fila_lista = next((r for r in listado_enriquecido if r["id"] == resumen_1), None)
    caso("listar_enriquecida() trae account_name", True, "account_name" in fila_lista.keys())

    print("\n--- actualizar_totales() — campos opcionales sin tocar cuando no se pasan ---")
    repo.actualizar_totales(resumen_1, monto_consumos_minor=100000, monto_total_pagado_minor=105000)
    fila_1_tras_totales = repo.obtener_por_id(resumen_1)
    caso("actualizar_totales() aplica monto_consumos_minor", 100000, fila_1_tras_totales["monto_consumos_minor"])
    caso("actualizar_totales() aplica monto_total_pagado_minor", 105000, fila_1_tras_totales["monto_total_pagado_minor"])
    caso("actualizar_totales() sin pasar monto_impuestos_minor no lo toca (sigue en 0)", 0, fila_1_tras_totales["monto_impuestos_minor"])
    caso("actualizar_totales() sin pasar porcentaje_impuesto_bp no lo toca (sigue en 500)", 500, fila_1_tras_totales["porcentaje_impuesto_bp"])

    repo.actualizar_totales(
        resumen_1, monto_consumos_minor=200000, monto_total_pagado_minor=210000,
        monto_impuestos_minor=10000, porcentaje_impuesto_bp=1000,
    )
    fila_1_tras_totales_2 = repo.obtener_por_id(resumen_1)
    caso("actualizar_totales() con todos los campos: monto_consumos_minor se actualiza", 200000, fila_1_tras_totales_2["monto_consumos_minor"])
    caso("actualizar_totales() con todos los campos: monto_impuestos_minor se actualiza", 10000, fila_1_tras_totales_2["monto_impuestos_minor"])
    caso("actualizar_totales() con todos los campos: porcentaje_impuesto_bp se actualiza", 1000, fila_1_tras_totales_2["porcentaje_impuesto_bp"])

    print("\n--- actualizar_totales(conn=...) — participa de una transacción externa ---")
    conn_externo = manager.conn
    with manager.transaction():
        repo.actualizar_totales(resumen_2, monto_consumos_minor=50000, monto_total_pagado_minor=52500, conn=conn_externo)
    caso("actualizar_totales(conn=...) persiste tras comitear", 50000, repo.obtener_por_id(resumen_2)["monto_consumos_minor"])

    print("\n--- marcar_cerrado() — CARGOS EXTRA DE RESUMEN: calcula los tres totales ---")
    # Armamos un escenario real: una compra en cuotas con 3 cuotas de 100000
    # cada una, las 3 atadas a resumen_1 (en_resumen), y 3 cargos extra en
    # resumen_1 (dos positivos, uno negativo) que suman 30000 neto.
    compra_1 = compras_repo.crear(
        fecha_compra="2026-01-05",
        concepto="Compra para resumen 1",
        cuenta_id=cuenta_a,
        categoria_id=cat_egreso,
        moneda_id=moneda_ars,
        monto_total_minor=300000,
        total_cuotas=3,
        monto_por_cuota_minor=100000,
    )
    cuotas_ids = cuotas_repo.crear_lote(
        compra_id=compra_1,
        cuotas=[
            {"numero_cuota": 1, "mes_proyectado": 1, "anio_proyectado": 2026, "monto_cuota_minor": 100000},
            {"numero_cuota": 2, "mes_proyectado": 1, "anio_proyectado": 2026, "monto_cuota_minor": 100000},
            {"numero_cuota": 3, "mes_proyectado": 1, "anio_proyectado": 2026, "monto_cuota_minor": 100000},
        ],
    )
    for cuota_id in cuotas_ids:
        cuotas_repo.marcar_estado(cuota_id, "en_resumen", resumen_id=resumen_1)

    cargos_repo.agregar(resumen_1, concepto="IVA", tipo="impuesto", monto_minor=25000)
    cargos_repo.agregar(resumen_1, concepto="Impuesto sellos", tipo="impuesto", monto_minor=10000)
    cargos_repo.agregar(resumen_1, concepto="Ajuste a favor", tipo="ajuste", monto_minor=-5000)
    # monto_consumos_minor esperado: 100000*3 = 300000
    # monto_impuestos_minor esperado: 25000 + 10000 - 5000 = 30000
    # porcentaje_impuesto_bp esperado: 30000*10000 // 300000 = 1000 (10%)

    resultado_cierre = repo.marcar_cerrado(resumen_1)
    caso("marcar_cerrado() devuelve monto_consumos_minor calculado", 300000, resultado_cierre["monto_consumos_minor"])
    caso("marcar_cerrado() devuelve monto_impuestos_minor calculado (neto con ajuste negativo)", 30000, resultado_cierre["monto_impuestos_minor"])
    caso("marcar_cerrado() devuelve porcentaje_impuesto_bp derivado", 1000, resultado_cierre["porcentaje_impuesto_bp"])

    fila_1_cerrado = repo.obtener_por_id(resumen_1)
    caso("marcar_cerrado() persiste estado='cerrado'", "cerrado", fila_1_cerrado["estado"])
    caso("marcar_cerrado() persiste monto_consumos_minor en la fila", 300000, fila_1_cerrado["monto_consumos_minor"])
    caso("marcar_cerrado() persiste monto_impuestos_minor en la fila", 30000, fila_1_cerrado["monto_impuestos_minor"])
    caso("marcar_cerrado() persiste porcentaje_impuesto_bp en la fila", 1000, fila_1_cerrado["porcentaje_impuesto_bp"])

    print("\n--- marcar_cerrado() — sin cuotas ni cargos: todo en 0, sin división por cero ---")
    resultado_cierre_2 = repo.marcar_cerrado(resumen_2)
    caso("marcar_cerrado() sin consumos: monto_consumos_minor da 0", 0, resultado_cierre_2["monto_consumos_minor"])
    caso("marcar_cerrado() sin cargos: monto_impuestos_minor da 0", 0, resultado_cierre_2["monto_impuestos_minor"])
    caso("marcar_cerrado() sin consumos: porcentaje_impuesto_bp da 0 (no explota por división por cero)", 0, resultado_cierre_2["porcentaje_impuesto_bp"])

    print("\n--- marcar_cerrado(conn=...) — participa de una transacción externa ---")
    with manager.transaction():
        resultado_cierre_3 = repo.marcar_cerrado(resumen_3, conn=conn_externo)
    caso("marcar_cerrado(conn=...) persiste tras comitear", "cerrado", repo.obtener_por_id(resumen_3)["estado"])
    caso("marcar_cerrado(conn=...) devuelve el dict de todas formas", 0, resultado_cierre_3["monto_consumos_minor"])

    print("\n--- marcar_cerrado(conn=...) — conn GENUINAMENTE distinto a self._db.conn ---")
    # Prueba real de que marcar_cerrado() respeta el conn recibido para
    # TODAS sus operaciones, no solo para el UPDATE final: abrimos una
    # SEGUNDA conexión sqlite3 real al mismo archivo de la dummy DB (no la
    # misma que self._db.conn), insertamos cuotas_credito y
    # resumen_cargos_extra en ESA segunda conexión SIN comitear, y llamamos
    # marcar_cerrado(conn=esa_segunda_conexion). Si el método leyera contra
    # self._db.conn (el bug corregido en esta tarea), no vería estos
    # INSERTs no comiteados — self._db.conn es una conexión distinta y en
    # sqlite3 los cambios no comiteados de una conexión no son visibles
    # para otra — y calcularía 0 en vez de los valores reales. Este test
    # falla de la forma correcta si el bug reaparece.
    resumen_4 = repo.crear(cuenta_id=cuenta_a, mes=5, anio=2026)
    compra_2 = compras_repo.crear(
        fecha_compra="2026-05-05",
        concepto="Compra para prueba de conn genuino",
        cuenta_id=cuenta_a,
        categoria_id=cat_egreso,
        moneda_id=moneda_ars,
        monto_total_minor=100000,
        total_cuotas=2,
        monto_por_cuota_minor=50000,
    )

    conn_2 = sqlite3.connect(str(db_path))
    conn_2.row_factory = sqlite3.Row
    conn_2.execute(
        "INSERT INTO cuotas_credito "
        "(compra_id, resumen_id, numero_cuota, mes_proyectado, anio_proyectado, monto_cuota_minor, estado) "
        "VALUES (?, ?, 1, 5, 2026, 50000, 'en_resumen');",
        (compra_2, resumen_4),
    )
    conn_2.execute(
        "INSERT INTO cuotas_credito "
        "(compra_id, resumen_id, numero_cuota, mes_proyectado, anio_proyectado, monto_cuota_minor, estado) "
        "VALUES (?, ?, 2, 5, 2026, 50000, 'en_resumen');",
        (compra_2, resumen_4),
    )
    conn_2.execute(
        "INSERT INTO resumen_cargos_extra (resumen_id, concepto, tipo, monto_minor) "
        "VALUES (?, 'IVA prueba conn', 'impuesto', 12000);",
        (resumen_4,),
    )
    # Deliberadamente NO comiteamos conn_2 todavía — la transacción queda
    # abierta (Python sqlite3 con isolation_level por default abre la
    # transacción implícitamente en el primer INSERT).

    caso(
        "self._db.conn NO ve las cuotas_credito no comiteadas de conn_2 (aislamiento real entre conexiones)",
        0,
        manager.fetchone("SELECT COUNT(*) AS n FROM cuotas_credito WHERE resumen_id = ?;", (resumen_4,))["n"],
    )
    caso(
        "self._db.conn NO ve los resumen_cargos_extra no comiteados de conn_2",
        0,
        manager.fetchone("SELECT COUNT(*) AS n FROM resumen_cargos_extra WHERE resumen_id = ?;", (resumen_4,))["n"],
    )

    resultado_cierre_4 = repo.marcar_cerrado(resumen_4, conn=conn_2)
    caso(
        "marcar_cerrado(conn=conn_2) SÍ ve los datos no comiteados de conn_2: monto_consumos_minor",
        100000,
        resultado_cierre_4["monto_consumos_minor"],
    )
    caso(
        "marcar_cerrado(conn=conn_2) SÍ ve los datos no comiteados de conn_2: monto_impuestos_minor",
        12000,
        resultado_cierre_4["monto_impuestos_minor"],
    )
    caso(
        "marcar_cerrado(conn=conn_2) calcula porcentaje_impuesto_bp con los datos no comiteados de conn_2",
        1200,
        resultado_cierre_4["porcentaje_impuesto_bp"],
    )

    conn_2.commit()
    conn_2.close()

    fila_4_tras_commit = repo.obtener_por_id(resumen_4)
    caso("tras comitear conn_2, self._db.conn ve el resumen cerrado", "cerrado", fila_4_tras_commit["estado"])
    caso("tras comitear conn_2, monto_consumos_minor persistido es correcto", 100000, fila_4_tras_commit["monto_consumos_minor"])
    caso("tras comitear conn_2, monto_impuestos_minor persistido es correcto", 12000, fila_4_tras_commit["monto_impuestos_minor"])
    caso("tras comitear conn_2, porcentaje_impuesto_bp persistido es correcto", 1200, fila_4_tras_commit["porcentaje_impuesto_bp"])

    print("\n--- marcar_pagado() — CARGOS EXTRA DE RESUMEN: ahora sí escribe monto_total_pagado_minor ---")
    repo.marcar_pagado(resumen_1, fecha_pago="2026-01-20", monto_pagado_minor=330000)
    fila_1_pagado = repo.obtener_por_id(resumen_1)
    caso("marcar_pagado() deja estado='pagado'", "pagado", fila_1_pagado["estado"])
    caso("marcar_pagado() setea fecha_pago", "2026-01-20", fila_1_pagado["fecha_pago"])
    caso("marcar_pagado() escribe monto_total_pagado_minor = monto_pagado_minor pasado", 330000, fila_1_pagado["monto_total_pagado_minor"])

    print("\n--- marcar_pagado(conn=...) — participa de una transacción externa ---")
    with manager.transaction():
        repo.marcar_pagado(resumen_2, fecha_pago="2026-02-20", monto_pagado_minor=0, conn=conn_externo)
    caso("marcar_pagado(conn=...) persiste tras comitear", "pagado", repo.obtener_por_id(resumen_2)["estado"])
    caso("marcar_pagado(conn=...) escribe monto_total_pagado_minor también con conn", 0, repo.obtener_por_id(resumen_2)["monto_total_pagado_minor"])

    manager.desconectar()

    print(f"\n--- Resumen: {casos_ok}/{casos_total} casos OK ---")


if __name__ == "__main__":
    main()
