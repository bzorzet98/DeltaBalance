"""
verify/presupuestos_ingresos_empleos/verify_empleos_repository.py

Verifica EmpleosRepository (repositories/empleos_repository.py): empleos
(crear/obtener/listar con filtro solo_activos), recibos_sueldo (crear,
obtener_por_periodo) y descuentos_programados (crear, listar_pendientes,
marcar_aplicado).

El caso que más importa de este verify es la atomicidad REAL de
crear_recibo(conn=...): simula exactamente el flujo que un futuro
EmpleosService va a orquestar (INSERT en transacciones — acá hecho a mano
con SQL directo, simulando lo que sería
TransaccionesRepository.crear(conn=...) — + crear_recibo(transaccion_id=...,
conn=...) en la misma transacción externa), con éxito y con rollback
simulado.

También confirma el HALLAZGO documentado en el docstring del repositorio:
marcar_descuento_aplicado() es una escritura de UNA sola tabla
(descuentos_programados) — NO toca ninguna columna de recibos_sueldo. Se
prueba explícitamente leyendo el recibo antes y después de aplicar el
descuento y confirmando que no cambió.

listar_empleos(solo_activos=False) necesita al menos un empleo inactivo
para el filtro cruzado; como EmpleosRepository no expone (todavía) un
método para desactivar un empleo, ese único INSERT se hace con SQL directo
en este script — no hay otra forma de armar el escenario sin inventar un
método que nadie pidió.

Correlo con:
    python verify/presupuestos_ingresos_empleos/verify_empleos_repository.py
"""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from verify._dummy_db import crear_dummy_db
from db.database import DatabaseManager
from repositories.cuentas_repository import CuentasRepository
from repositories.empleos_repository import EmpleosRepository


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
    repo = EmpleosRepository(manager)
    cuentas_repo = CuentasRepository(manager)

    moneda_ars = manager.fetchone("SELECT id FROM monedas WHERE codigo = 'ARS';")["id"]
    cuenta_id = cuentas_repo.crear(nombre="Cuenta Sueldo", tipo="debito", moneda_codigo="ARS")
    categoria_ingreso = manager.fetchone("SELECT id FROM categorias WHERE tipo = 'ingreso' LIMIT 1;")["id"]

    print("--- crear_empleo() ---")
    empleo_1 = repo.crear_empleo(nombre_empresa="Acme SA", moneda_id=moneda_ars, puesto="Backend")
    empleo_2 = repo.crear_empleo(
        nombre_empresa="Beta SRL", moneda_id=moneda_ars,
        porcentaje_jubilacion=1200, porcentaje_obra_social=350, porcentaje_gremio=100, tope_copago_os_minor=5000,
    )
    caso("crear_empleo() devuelve ids numéricos distintos", True, isinstance(empleo_1, int) and isinstance(empleo_2, int) and empleo_1 != empleo_2)

    fila_1_cruda = manager.fetchone("SELECT * FROM empleos WHERE id = ?;", (empleo_1,))
    caso("crear_empleo() persiste porcentaje_jubilacion default (1100)", 1100, fila_1_cruda["porcentaje_jubilacion"])
    caso("crear_empleo() persiste porcentaje_obra_social default (300)", 300, fila_1_cruda["porcentaje_obra_social"])
    caso("crear_empleo() deja activa=1 por default de columna", 1, fila_1_cruda["activa"])

    fila_2_cruda = manager.fetchone("SELECT * FROM empleos WHERE id = ?;", (empleo_2,))
    caso("crear_empleo() persiste porcentaje_jubilacion custom", 1200, fila_2_cruda["porcentaje_jubilacion"])
    caso("crear_empleo() persiste tope_copago_os_minor custom", 5000, fila_2_cruda["tope_copago_os_minor"])

    print("\n--- obtener_empleo_por_id() ---")
    caso("obtener_empleo_por_id() encuentra el empleo 1", "Acme SA", repo.obtener_empleo_por_id(empleo_1)["nombre_empresa"])
    caso("obtener_empleo_por_id() de un id inexistente devuelve None", None, repo.obtener_empleo_por_id(999999))

    print("\n--- listar_empleos() — filtro solo_activos ---")
    empleo_inactivo = manager.execute(
        "INSERT INTO empleos (nombre_empresa, moneda_id, activa) VALUES ('Empresa Vieja', ?, 0);",
        (moneda_ars,),
    )
    listado_activos = repo.listar_empleos(solo_activos=True)
    ids_activos = [e["id"] for e in listado_activos]
    caso("listar_empleos(solo_activos=True) incluye empleo_1 y empleo_2", True, empleo_1 in ids_activos and empleo_2 in ids_activos)
    caso("listar_empleos(solo_activos=True) excluye el empleo inactivo", False, empleo_inactivo in ids_activos)

    listado_todos = repo.listar_empleos(solo_activos=False)
    ids_todos = [e["id"] for e in listado_todos]
    caso("listar_empleos(solo_activos=False) incluye el empleo inactivo", True, empleo_inactivo in ids_todos)

    print("\n--- crear_recibo() — sin transacción asociada ---")
    recibo_1 = repo.crear_recibo(
        empleo_id=empleo_1, mes=1, anio=2026,
        sueldo_bruto_minor=100000000, desc_jubilacion_minor=11000000,
        desc_obra_social_minor=3000000, monto_neto_final_minor=86000000,
    )
    caso("crear_recibo() devuelve un id numérico", True, isinstance(recibo_1, int))

    fila_recibo_cruda = manager.fetchone("SELECT * FROM recibos_sueldo WHERE id = ?;", (recibo_1,))
    caso("crear_recibo() persiste monto_neto_final_minor tal cual se pasó", 86000000, fila_recibo_cruda["monto_neto_final_minor"])
    caso("crear_recibo() sin transaccion_id lo deja NULL", None, fila_recibo_cruda["transaccion_id"])
    caso("crear_recibo() sin desc_copagos_os_minor lo deja en 0 (default)", 0, fila_recibo_cruda["desc_copagos_os_minor"])

    print("\n--- obtener_recibo_por_periodo() ---")
    caso("obtener_recibo_por_periodo() encuentra el recibo por el UNIQUE", recibo_1, repo.obtener_recibo_por_periodo(empleo_1, 1, 2026)["id"])
    caso("obtener_recibo_por_periodo() de un período sin recibo devuelve None", None, repo.obtener_recibo_por_periodo(empleo_1, 2, 2026))
    caso("obtener_recibo_por_periodo() no confunde empleo_1 con empleo_2", None, repo.obtener_recibo_por_periodo(empleo_2, 1, 2026))

    print("\n--- crear_recibo(conn=...) — atomicidad REAL con una transacción simulada ---")
    # Simula lo que un futuro EmpleosService va a orquestar: crear la
    # transacción de ingreso (acá con SQL directo, en lugar de
    # TransaccionesRepository.crear(conn=...) que todavía no se invoca desde
    # este bloque) y el recibo, ambos en la misma transacción externa.
    conn_externo = manager.conn
    with manager.transaction():
        t_id = conn_externo.execute(
            """
            INSERT INTO transacciones (fecha, concepto, cuenta_id, categoria_id, moneda_id, tipo_movimiento, monto_minor)
            VALUES ('2026-02-01', 'Sueldo 02/2026', ?, ?, ?, 'ingreso', 86000000);
            """,
            (cuenta_id, categoria_ingreso, moneda_ars),
        ).lastrowid
        recibo_2 = repo.crear_recibo(
            empleo_id=empleo_1, mes=2, anio=2026,
            sueldo_bruto_minor=100000000, desc_jubilacion_minor=11000000,
            desc_obra_social_minor=3000000, monto_neto_final_minor=86000000,
            transaccion_id=t_id, conn=conn_externo,
        )
    fila_recibo_2 = manager.fetchone("SELECT * FROM recibos_sueldo WHERE id = ?;", (recibo_2,))
    caso("crear_recibo(conn=...) persiste tras comitear", True, fila_recibo_2 is not None)
    caso("crear_recibo(conn=...) persiste transaccion_id vinculado", t_id, fila_recibo_2["transaccion_id"])
    caso("la transacción vinculada también persistió", True, manager.fetchone("SELECT * FROM transacciones WHERE id = ?;", (t_id,)) is not None)

    print("\n--- crear_recibo(conn=...) — rollback simulado (transacción + recibo, ninguno debe persistir) ---")
    transacciones_antes = manager.fetchone("SELECT COUNT(*) AS n FROM transacciones;")["n"]
    recibos_antes = manager.fetchone("SELECT COUNT(*) AS n FROM recibos_sueldo;")["n"]
    try:
        with manager.transaction():
            t_id_fallido = conn_externo.execute(
                """
                INSERT INTO transacciones (fecha, concepto, cuenta_id, categoria_id, moneda_id, tipo_movimiento, monto_minor)
                VALUES ('2026-03-01', 'Sueldo que va a fallar', ?, ?, ?, 'ingreso', 1);
                """,
                (cuenta_id, categoria_ingreso, moneda_ars),
            ).lastrowid
            repo.crear_recibo(
                empleo_id=empleo_1, mes=3, anio=2026,
                sueldo_bruto_minor=1, desc_jubilacion_minor=0, desc_obra_social_minor=0,
                monto_neto_final_minor=1, transaccion_id=t_id_fallido, conn=conn_externo,
            )
            raise RuntimeError("Fallo simulado a mitad de la transacción externa")
    except RuntimeError:
        pass
    transacciones_despues = manager.fetchone("SELECT COUNT(*) AS n FROM transacciones;")["n"]
    recibos_despues = manager.fetchone("SELECT COUNT(*) AS n FROM recibos_sueldo;")["n"]
    caso("rollback revierte el INSERT de la transacción", transacciones_antes, transacciones_despues)
    caso("rollback revierte el INSERT del recibo (ninguno queda huérfano del otro)", recibos_antes, recibos_despues)

    print("\n--- crear_descuento_programado() ---")
    desc_1 = repo.crear_descuento_programado(concepto="Anticipo", monto_minor=500000, mes_aplicacion=1, anio_aplicacion=2026)
    desc_2 = repo.crear_descuento_programado(concepto="Préstamo empresa", monto_minor=300000, mes_aplicacion=1, anio_aplicacion=2026, notas="Cuota 3/12")
    desc_3 = repo.crear_descuento_programado(concepto="Otro mes", monto_minor=100000, mes_aplicacion=2, anio_aplicacion=2026)
    caso("crear_descuento_programado() devuelve ids numéricos distintos", True, len({desc_1, desc_2, desc_3}) == 3)

    fila_desc_1 = manager.fetchone("SELECT * FROM descuentos_programados WHERE id = ?;", (desc_1,))
    caso("crear_descuento_programado() deja estado='pendiente' por default de columna", "pendiente", fila_desc_1["estado"])

    print("\n--- listar_descuentos_pendientes() — filtro cruzado ---")
    pendientes_enero = repo.listar_descuentos_pendientes(1, 2026)
    ids_pendientes_enero = [d["id"] for d in pendientes_enero]
    caso("listar_descuentos_pendientes(1, 2026) incluye desc_1 y desc_2", True, desc_1 in ids_pendientes_enero and desc_2 in ids_pendientes_enero)
    caso("listar_descuentos_pendientes(1, 2026) excluye desc_3 (es de febrero)", False, desc_3 in ids_pendientes_enero)

    print("\n--- marcar_descuento_aplicado() — HALLAZGO: no toca recibos_sueldo ---")
    recibo_antes_de_aplicar = manager.fetchone("SELECT * FROM recibos_sueldo WHERE id = ?;", (recibo_1,))
    repo.marcar_descuento_aplicado(desc_1, recibo_id=recibo_1)
    fila_desc_1_aplicado = manager.fetchone("SELECT * FROM descuentos_programados WHERE id = ?;", (desc_1,))
    caso("marcar_descuento_aplicado() deja estado='aplicado'", "aplicado", fila_desc_1_aplicado["estado"])
    caso("marcar_descuento_aplicado() persiste recibo_id", recibo_1, fila_desc_1_aplicado["recibo_id"])

    recibo_despues_de_aplicar = manager.fetchone("SELECT * FROM recibos_sueldo WHERE id = ?;", (recibo_1,))
    caso(
        "marcar_descuento_aplicado() NO toca ninguna columna de recibos_sueldo (hallazgo: es de una sola tabla)",
        {k: recibo_antes_de_aplicar[k] for k in recibo_antes_de_aplicar.keys()},
        {k: recibo_despues_de_aplicar[k] for k in recibo_despues_de_aplicar.keys()},
    )

    pendientes_enero_tras_aplicar = repo.listar_descuentos_pendientes(1, 2026)
    caso(
        "el descuento aplicado ya no aparece en listar_descuentos_pendientes()",
        False,
        desc_1 in [d["id"] for d in pendientes_enero_tras_aplicar],
    )

    print("\n--- marcar_descuento_aplicado(conn=...) — participa de una transacción externa ---")
    with manager.transaction():
        repo.marcar_descuento_aplicado(desc_2, recibo_id=recibo_1, conn=conn_externo)
    caso("marcar_descuento_aplicado(conn=...) persiste tras comitear", "aplicado", manager.fetchone("SELECT estado FROM descuentos_programados WHERE id = ?;", (desc_2,))["estado"])

    print("\n--- marcar_descuento_aplicado(conn=...) — rollback simulado ---")
    try:
        with manager.transaction():
            repo.marcar_descuento_aplicado(desc_3, recibo_id=recibo_1, conn=conn_externo)
            raise RuntimeError("Fallo simulado a mitad de la transacción externa")
    except RuntimeError:
        pass
    caso(
        "marcar_descuento_aplicado(conn=...) con rollback: el descuento sigue 'pendiente'",
        "pendiente",
        manager.fetchone("SELECT estado FROM descuentos_programados WHERE id = ?;", (desc_3,))["estado"],
    )

    manager.desconectar()

    print(f"\n--- Resumen: {casos_ok}/{casos_total} casos OK ---")


if __name__ == "__main__":
    main()
