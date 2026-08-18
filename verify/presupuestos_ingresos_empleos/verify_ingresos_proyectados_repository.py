"""
verify/presupuestos_ingresos_empleos/verify_ingresos_proyectados_repository.py

Verifica IngresosProyectadosRepository
(repositories/ingresos_proyectados_repository.py): crear, obtener_por_id,
listar_por_periodo con filtros cruzados, actualizar con el sentinel
NO_CAMBIAR, marcar_estado para las tres transiciones (pendiente/cobrado/
parcial) — incluyendo la transición que NO toca monto_percibido_minor
cuando no se pasa — y atomicidad con conn + rollback.

Nota: a diferencia de otros repositorios de esta fase, acá NO se prueba
"actualizar(campo=None) escribe NULL explícito" — ninguna de las columnas
que actualizar() toca (concepto, mes, anio, monto_estimado_minor,
moneda_id) es NULLable en el schema (todas son NOT NULL), así que ese
escenario no tiene un resultado válido que probar (rompería una constraint,
no es un caso de negocio real).

Correlo con:
    python verify/presupuestos_ingresos_empleos/verify_ingresos_proyectados_repository.py
"""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from verify._dummy_db import crear_dummy_db
from db.database import DatabaseManager
from repositories.ingresos_proyectados_repository import IngresosProyectadosRepository


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
    repo = IngresosProyectadosRepository(manager)

    moneda_ars = manager.fetchone("SELECT id FROM monedas WHERE codigo = 'ARS';")["id"]
    moneda_usd = manager.fetchone("SELECT id FROM monedas WHERE codigo = 'USD';")["id"]

    print("--- crear() ---")
    ingreso_1 = repo.crear(concepto="Freelance", mes=1, anio=2026, monto_estimado_minor=200000, moneda_id=moneda_ars)
    ingreso_2 = repo.crear(concepto="Reintegro", mes=1, anio=2026, monto_estimado_minor=50000, moneda_id=moneda_usd)
    ingreso_3 = repo.crear(concepto="Venta usado", mes=2, anio=2026, monto_estimado_minor=30000, moneda_id=moneda_ars)
    caso(
        "crear() devuelve ids numéricos distintos",
        True,
        len({ingreso_1, ingreso_2, ingreso_3}) == 3 and all(isinstance(x, int) for x in (ingreso_1, ingreso_2, ingreso_3)),
    )

    fila_cruda = manager.fetchone("SELECT * FROM ingresos_proyectados WHERE id = ?;", (ingreso_1,))
    caso("crear() deja estado='pendiente' por default de columna", "pendiente", fila_cruda["estado"])
    caso("crear() arranca monto_percibido_minor en 0", 0, fila_cruda["monto_percibido_minor"])

    print("\n--- obtener_por_id() ---")
    caso("obtener_por_id() encuentra el ingreso 1", "Freelance", repo.obtener_por_id(ingreso_1)["concepto"])
    caso("obtener_por_id() de un id inexistente devuelve None", None, repo.obtener_por_id(999999))

    print("\n--- listar_por_periodo() — filtro cruzado ---")
    listado_enero = repo.listar_por_periodo(1, 2026)
    ids_enero = [r["id"] for r in listado_enero]
    caso("listar_por_periodo(1, 2026) incluye ingreso_1 e ingreso_2", True, ingreso_1 in ids_enero and ingreso_2 in ids_enero)
    caso("listar_por_periodo(1, 2026) excluye ingreso_3 (es de febrero)", False, ingreso_3 in ids_enero)

    listado_marzo = repo.listar_por_periodo(3, 2026)
    caso("listar_por_periodo(3, 2026) no incluye nada", 0, len(listado_marzo))

    print("\n--- actualizar() — sentinel NO_CAMBIAR ---")
    repo.actualizar(ingreso_1, monto_estimado_minor=250000)
    fila_1 = repo.obtener_por_id(ingreso_1)
    caso("actualizar(monto_estimado_minor=...) solo: el monto cambia", 250000, fila_1["monto_estimado_minor"])
    caso("actualizar(monto_estimado_minor=...) solo: concepto mantiene su valor previo (NO_CAMBIAR)", "Freelance", fila_1["concepto"])
    caso("actualizar(monto_estimado_minor=...) solo: mes mantiene su valor previo (NO_CAMBIAR)", 1, fila_1["mes"])

    repo.actualizar(ingreso_1, concepto="Freelance (renovado)", moneda_id=moneda_usd)
    fila_1_v2 = repo.obtener_por_id(ingreso_1)
    caso("actualizar(concepto=..., moneda_id=...): ambos cambian", True, fila_1_v2["concepto"] == "Freelance (renovado)" and fila_1_v2["moneda_id"] == moneda_usd)
    caso("actualizar(concepto=..., moneda_id=...): monto_estimado_minor mantiene su valor previo", 250000, fila_1_v2["monto_estimado_minor"])

    sin_cambios = repo.actualizar(ingreso_1)
    caso("actualizar() sin ningún campo devuelve False", False, sin_cambios)

    caso_excepcion_no_afecta_estado = repo.obtener_por_id(ingreso_1)["estado"]
    caso("actualizar() nunca toca estado (no es uno de sus parámetros)", "pendiente", caso_excepcion_no_afecta_estado)

    print("\n--- marcar_estado() — transición a 'parcial' con monto_percibido_minor ---")
    repo.marcar_estado(ingreso_2, "parcial", monto_percibido_minor=20000)
    fila_2 = repo.obtener_por_id(ingreso_2)
    caso("marcar_estado() aplica el nuevo estado", "parcial", fila_2["estado"])
    caso("marcar_estado() aplica monto_percibido_minor cuando se pasa", 20000, fila_2["monto_percibido_minor"])

    print("\n--- marcar_estado() — transición a 'cobrado' con el monto final ---")
    repo.marcar_estado(ingreso_2, "cobrado", monto_percibido_minor=50000)
    fila_2_v2 = repo.obtener_por_id(ingreso_2)
    caso("marcar_estado() actualiza estado a 'cobrado'", "cobrado", fila_2_v2["estado"])
    caso("marcar_estado() actualiza monto_percibido_minor al nuevo valor", 50000, fila_2_v2["monto_percibido_minor"])

    print("\n--- marcar_estado() — SIN monto_percibido_minor: no lo toca ---")
    repo.marcar_estado(ingreso_3, "pendiente")
    fila_3 = repo.obtener_por_id(ingreso_3)
    caso("marcar_estado() sin monto_percibido_minor: estado igual se aplica", "pendiente", fila_3["estado"])
    caso("marcar_estado() sin monto_percibido_minor: NO toca la columna (sigue en 0)", 0, fila_3["monto_percibido_minor"])

    print("\n--- crear()/actualizar()/marcar_estado()(conn=...) — participan de una transacción externa ---")
    conn_externo = manager.conn
    with manager.transaction():
        ingreso_4 = repo.crear(concepto="Bono", mes=4, anio=2026, monto_estimado_minor=80000, moneda_id=moneda_ars, conn=conn_externo)
        repo.marcar_estado(ingreso_4, "cobrado", monto_percibido_minor=80000, conn=conn_externo)
    fila_4 = repo.obtener_por_id(ingreso_4)
    caso("crear(conn=...) + marcar_estado(conn=...) persisten tras comitear", "cobrado", fila_4["estado"] if fila_4 else None)
    caso("marcar_estado(conn=...) persiste monto_percibido_minor tras comitear", 80000, fila_4["monto_percibido_minor"] if fila_4 else None)

    print("\n--- crear()/marcar_estado()(conn=...) — rollback simulado ---")
    ingresos_antes = manager.fetchone("SELECT COUNT(*) AS n FROM ingresos_proyectados;")["n"]
    try:
        with manager.transaction():
            ingreso_5 = repo.crear(concepto="No debería persistir", mes=5, anio=2026, monto_estimado_minor=1, moneda_id=moneda_ars, conn=conn_externo)
            repo.marcar_estado(ingreso_5, "cobrado", monto_percibido_minor=1, conn=conn_externo)
            raise RuntimeError("Fallo simulado a mitad de la transacción externa")
    except RuntimeError:
        pass
    ingresos_despues = manager.fetchone("SELECT COUNT(*) AS n FROM ingresos_proyectados;")["n"]
    caso("rollback revierte el INSERT (no queda huérfano)", ingresos_antes, ingresos_despues)

    manager.desconectar()

    print(f"\n--- Resumen: {casos_ok}/{casos_total} casos OK ---")


if __name__ == "__main__":
    main()
