"""
verify/ahorros/verify_movimientos_manuales.py

Verifica lo que agrega la tabla de movimientos de Ahorros e Inversiones
(ui/screens/ahorros.py, carga a mano de datos históricos) en
services/savings_service.py:

1. registrar_*(crear_transaccion=False): el movimiento queda informal
   (transaccion_id NULL) aunque el activo tenga cuenta — y sin el flag,
   como siempre, sí se crea la transacción del Registro. Las notas se
   guardan y list_movimientos() las devuelve.
2. update_movement():
   - aporte informal: el monto cambia y las asignaciones se rehacen con
     los MISMOS porcentajes que ya tenía (los objetivos son de cada
     movimiento: ver verify_objetivos_por_movimiento.py).
   - compra por monto bruto (docs/DATA_MODEL_DECISIONS.md sección 33): el
     total (monto + comisión) y el precio (monto / cantidad) se
     recalculan; el precio no se edita.
   - movimiento vinculado a una transacción: solo las notas.
   - un rendimiento no tiene comisión.
   - la tenencia no puede quedar negativa (unidades / saldo): rollback,
     nada cambia.
   - clave desconocida, id inexistente y "sin cambios".

Correlo con:
    python verify/ahorros/verify_movimientos_manuales.py
"""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from verify._dummy_db import crear_dummy_db
from db.database import DatabaseManager
from repositories.cuentas_repository import CuentasRepository
from services.savings_service import MovimientoNotFoundError, SavingsError, SavingsService


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

    def caso_excepcion(descripcion: str, tipo_esperado, callable_) -> None:
        nonlocal casos_ok, casos_total
        casos_total += 1
        try:
            callable_()
            print(f"❌ {descripcion} — esperaba {tipo_esperado.__name__}, no se lanzó ninguna excepción")
        except tipo_esperado as e:
            casos_ok += 1
            print(f"✅ {descripcion} — lanzó {tipo_esperado.__name__}: {e}")
        except Exception as e:
            print(f"❌ {descripcion} — esperaba {tipo_esperado.__name__}, se lanzó {type(e).__name__}: {e!r}")

    db_path = crear_dummy_db()
    print(f"Dummy DB creada en: {db_path}\n")

    manager = DatabaseManager(db_path=db_path)
    manager.inicializar()
    svc = SavingsService(manager)
    cuentas_repo = CuentasRepository(manager)

    moneda_ars = manager.fetchone("SELECT id FROM monedas WHERE codigo = 'ARS';")["id"]
    cuenta = cuentas_repo.crear(
        nombre="Banco (verify movimientos manuales)", tipo="debito", moneda_codigo="ARS", saldo_inicial=100000.0,
    )

    def transacciones_vivas() -> int:
        return manager.fetchone("SELECT COUNT(*) AS n FROM transacciones WHERE deleted_at IS NULL;")["n"]

    def movimiento(movimiento_id: str):
        return manager.fetchone("SELECT * FROM movimientos_activo WHERE id = ?;", (movimiento_id,))

    def asignaciones(movimiento_id: str) -> list[tuple[str, float, int]]:
        filas = manager.fetchall(
            "SELECT objetivo_id, porcentaje, monto_asignado_minor FROM asignaciones "
            "WHERE movimiento_id = ? ORDER BY porcentaje DESC;",
            (movimiento_id,),
        )
        return [(f["objetivo_id"], f["porcentaje"], f["monto_asignado_minor"]) for f in filas]

    # Un FCI y una acción CON cuenta: sin el flag, cada movimiento crearía su transacción.
    fci = svc.create_activo(nombre="FCI manual", tipo="fci", moneda_id=moneda_ars, cuenta_id=cuenta).entity_id
    accion = svc.create_activo(nombre="ACCION manual", tipo="accion", moneda_id=moneda_ars, cuenta_id=cuenta).entity_id
    obj_a = svc.create_objetivo(nombre="OBJETIVO A").entity_id
    obj_b = svc.create_objetivo(nombre="OBJETIVO B").entity_id
    reparto_60_40 = [{"objetivo_id": obj_a, "porcentaje": 60.0}, {"objetivo_id": obj_b, "porcentaje": 40.0}]

    print("--- 1. crear_transaccion=False: movimiento informal aunque el activo tenga cuenta ---")
    antes = transacciones_vivas()
    aporte = svc.registrar_aporte(
        fci, 100000, "2024-03-01", notas="  histórico del excel  ", crear_transaccion=False, asignaciones=reparto_60_40,
    )
    caso("aporte histórico: transaccion_id = None", None, aporte.data["transaccion_id"])
    caso("aporte histórico: no se creó ninguna transacción", antes, transacciones_vivas())
    caso("aporte histórico: notas guardadas (recortadas por el caller, acá tal cual)",
         "  histórico del excel  ", movimiento(aporte.entity_id)["notas"])
    caso("aporte histórico: asignaciones 60/40 elegidas en el movimiento",
         [(obj_a, 60.0, 60000), (obj_b, 40.0, 40000)], asignaciones(aporte.entity_id))

    compra = svc.registrar_compra(accion, 10, 1500000, 500, "2024-03-02", notas="compra vieja", crear_transaccion=False)
    caso("compra histórica: transaccion_id = None", None, compra.data["transaccion_id"])
    caso("compra histórica: monto = 10 × 1500.00 + 5.00 de comisión", 1500500, movimiento(compra.entity_id)["monto_total_minor"])
    caso("compra histórica: no se creó ninguna transacción", antes, transacciones_vivas())
    listados = {m["id"]: m for m in svc.list_movimientos(activo_id=accion)}
    caso("list_movimientos() devuelve las notas", "compra vieja", listados[compra.entity_id]["notas"])

    vinculado = svc.registrar_aporte(fci, 5000, "2026-01-10")
    caso("sin el flag (default): SÍ crea la transacción del Registro", True, vinculado.data["transaccion_id"] is not None)
    caso("sin el flag: hay una transacción más", antes + 1, transacciones_vivas())

    print("\n--- 2a. update_movement() sobre un aporte informal: monto + asignaciones con SUS porcentajes ---")
    resultado = svc.update_movement(aporte.entity_id, monto_minor=200000, fecha="2024-03-05", notas="")
    caso("update: success", True, resultado.success)
    caso("update: monto nuevo", 200000, movimiento(aporte.entity_id)["monto_total_minor"])
    caso("update: fecha nueva", "2024-03-05", movimiento(aporte.entity_id)["fecha"])
    caso("update: notas vacías = NULL", None, movimiento(aporte.entity_id)["notas"])
    caso("update: asignaciones rehechas 60/40 sobre el monto nuevo",
         [(obj_a, 60.0, 120000), (obj_b, 40.0, 80000)], asignaciones(aporte.entity_id))
    caso("update sin nada que cambiar: SIN CAMBIOS", "SIN CAMBIOS.",
         svc.update_movement(aporte.entity_id, monto_minor=200000).message)

    print("\n--- 2b. compra por monto bruto: el total y el precio se recalculan ---")
    svc.update_movement(compra.entity_id, cantidad=12)
    caso("cantidad 12: el monto bruto sigue en 15000.00, total = 15000.00 + 5.00",
         1500500, movimiento(compra.entity_id)["monto_total_minor"])
    caso("cantidad 12: precio calculado = 15000.00 / 12 = 1250.00", 125000, movimiento(compra.entity_id)["precio_unitario_minor"])
    svc.update_movement(compra.entity_id, monto_minor=1200000, comision_minor=0)
    caso("monto 12000.00 y sin comisión: total = 12000.00", 1200000, movimiento(compra.entity_id)["monto_total_minor"])
    caso("precio calculado = 12000.00 / 12 = 1000.00", 100000, movimiento(compra.entity_id)["precio_unitario_minor"])
    caso_excepcion("editar el precio unitario se rechaza (es calculado)", SavingsError,
                   lambda: svc.update_movement(compra.entity_id, precio_unitario_minor=1))
    caso_excepcion("cantidad no entera en una acción se rechaza", SavingsError,
                   lambda: svc.update_movement(compra.entity_id, cantidad=1.5))

    print("\n--- 2c. movimiento vinculado a una transacción: solo las notas ---")
    caso_excepcion("vinculado: cambiar la fecha se rechaza", SavingsError,
                   lambda: svc.update_movement(vinculado.entity_id, fecha="2026-01-11"))
    caso_excepcion("vinculado: cambiar el monto se rechaza", SavingsError,
                   lambda: svc.update_movement(vinculado.entity_id, monto_minor=1))
    svc.update_movement(vinculado.entity_id, notas="nota nueva")
    caso("vinculado: las notas sí se editan", "nota nueva", movimiento(vinculado.entity_id)["notas"])
    caso("vinculado: la fecha quedó igual", "2026-01-10", movimiento(vinculado.entity_id)["fecha"])

    print("\n--- 2d. un rendimiento no tiene comisión ---")
    rendimiento = svc.registrar_rendimiento(fci, 3000, "2024-04-01")
    caso_excepcion("comisión en un rendimiento se rechaza", SavingsError,
                   lambda: svc.update_movement(rendimiento.entity_id, comision_minor=100))

    print("\n--- 2e. la tenencia no puede quedar negativa (rollback) ---")
    venta = svc.registrar_venta(accion, 8, 1600000, 0, "2024-05-01", crear_transaccion=False)
    caso_excepcion("bajar la compra a 5 con 8 vendidas se rechaza", SavingsError,
                   lambda: svc.update_movement(compra.entity_id, cantidad=5))
    caso("tras el rechazo, la compra sigue en 12", 12, movimiento(compra.entity_id)["cantidad"])
    caso("tras el rechazo, el monto sigue igual", 1200000, movimiento(compra.entity_id)["monto_total_minor"])
    caso_excepcion("subir la venta a 13 con 12 compradas se rechaza", SavingsError,
                   lambda: svc.update_movement(venta.entity_id, cantidad=13))

    retiro = svc.registrar_retiro(fci, 50000, "2024-06-01", crear_transaccion=False)
    caso("retiro histórico: transaccion_id = None", None, retiro.data["transaccion_id"])
    caso_excepcion("subir el retiro por encima del saldo se rechaza", SavingsError,
                   lambda: svc.update_movement(retiro.entity_id, monto_minor=10_000_000))
    caso("tras el rechazo, el retiro sigue en 500.00", 50000, movimiento(retiro.entity_id)["monto_total_minor"])

    print("\n--- 2f. errores de forma ---")
    caso_excepcion("clave desconocida se rechaza", SavingsError,
                   lambda: svc.update_movement(aporte.entity_id, activo_id=accion))
    caso_excepcion("id inexistente → MovimientoNotFoundError", MovimientoNotFoundError,
                   lambda: svc.update_movement("no-existe", notas="x"))
    caso_excepcion("fecha inválida se rechaza", SavingsError,
                   lambda: svc.update_movement(aporte.entity_id, fecha="05/03/2024"))

    print(f"\n{casos_ok}/{casos_total} casos OK")
    print(f"(La DB temporal quedó en {db_path} — no es data/deltabalance.db.)")


if __name__ == "__main__":
    main()
