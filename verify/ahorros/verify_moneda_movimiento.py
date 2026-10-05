"""
verify/ahorros/verify_moneda_movimiento.py

Verifica la moneda por movimiento de Ahorros e Inversiones
(docs/DATA_MODEL_DECISIONS.md sección 33) en services/savings_service.py:

1. CEDEAR en ARS: compra sin moneda explícita → la del activo; compra y
   venta en USD → guardadas en USD. Compra / venta por MONTO bruto: el total
   es monto ± comisión y el precio se guarda calculado (monto / cantidad).
2. list_movimientos(): cada movimiento con SU moneda y su monto bruto.
3. Resumen por activo: unidades sin mezclar nada, precio promedio por
   moneda (primero la del activo); get_balance_por_tipo() separa ARS y USD.
4. FCI: la moneda es siempre la del activo — otra se rechaza sin escribir
   nada, al cargar y al editar.
5. update_movement(): cambiar la moneda de una compra de CEDEAR.
6. Transacción vinculada del Registro: en la moneda del movimiento.
7. Migración: el backfill de movimientos_activo.moneda_id (db/
   schema_migrations.py) pone la moneda del activo en las filas sin moneda.

Correlo con:
    python verify/ahorros/verify_moneda_movimiento.py
"""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from verify._dummy_db import crear_dummy_db
from db.database import DatabaseManager
from db.schema_migrations import MIGRACIONES_COLUMNA
from repositories.cuentas_repository import CuentasRepository
from repositories.movimientos_activo_repository import MovimientosActivoRepository
from services.savings_service import SavingsError, SavingsService


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
    ars = manager.fetchone("SELECT id FROM monedas WHERE codigo = 'ARS';")["id"]
    usd = manager.fetchone("SELECT id FROM monedas WHERE codigo = 'USD';")["id"]
    codigos = {ars: "ARS", usd: "USD"}

    def fila(movimiento_id: str):
        return manager.fetchone("SELECT * FROM movimientos_activo WHERE id = ?;", (movimiento_id,))

    def moneda_de(movimiento_id: str) -> str:
        return codigos.get(fila(movimiento_id)["moneda_id"], "?")

    def movimientos_totales() -> int:
        return manager.fetchone("SELECT COUNT(*) AS n FROM movimientos_activo;")["n"]

    def entrada(activo_id: str) -> dict:
        return next(e for grupo in svc.get_resumen_por_tipo().values() for e in grupo if e["activo_id"] == activo_id)

    nvda = svc.create_activo(nombre="NVDA", tipo="cedear", moneda_id=ars).entity_id

    print("--- 1. CEDEAR en ARS: compra en ARS, compra y venta en USD ---")
    compra_ars = svc.registrar_compra(nvda, 10, 10000000, 0, "2026-01-05", crear_transaccion=False).entity_id
    caso("compra sin moneda explícita: la del activo (ARS)", "ARS", moneda_de(compra_ars))
    caso("compra ARS: precio calculado = 100000.00 / 10", 1000000, fila(compra_ars)["precio_unitario_minor"])

    compra_usd = svc.registrar_compra(nvda, 5, 5000, 0, "2026-01-06", crear_transaccion=False, moneda_id=usd).entity_id
    caso("compra en USD: guardada en USD", "USD", moneda_de(compra_usd))
    caso("compra USD: precio calculado = 50.00 / 5", 1000, fila(compra_usd)["precio_unitario_minor"])

    venta_usd = svc.registrar_venta(nvda, 3, 3600, 100, "2026-01-07", crear_transaccion=False, moneda_id=usd).entity_id
    caso("venta en USD (otra moneda que la compra en ARS): guardada en USD", "USD", moneda_de(venta_usd))
    caso("venta: total = monto bruto 36.00 − comisión 1.00", 3500, fila(venta_usd)["monto_total_minor"])
    caso("venta: precio calculado = 36.00 / 3", 1200, fila(venta_usd)["precio_unitario_minor"])

    dividendo = svc.registrar_rendimiento(nvda, 200, "2026-01-08", asignaciones=[], moneda_id=usd).entity_id
    caso("dividendo en USD", "USD", moneda_de(dividendo))

    print("\n--- 2. list_movimientos(): cada uno con su moneda y su monto bruto ---")
    listados = {m["id"]: m for m in svc.list_movimientos(activo_id=nvda)}
    caso("monedas de los movimientos",
         ["ARS", "USD", "USD", "USD"],
         [codigos[listados[i]["moneda_id"]] for i in (compra_ars, compra_usd, venta_usd, dividendo)])
    caso("monto de la venta = el bruto (36.00), no el total (35.00)", 3600, listados[venta_usd]["monto_minor"])
    caso("monto del dividendo = su total", 200, listados[dividendo]["monto_minor"])

    print("\n--- 3. Resumen por activo ---")
    e = entrada(nvda)
    caso("unidades: 10 + 5 − 3 (las monedas no importan)", 12, e["unidades"])
    caso("precio promedio por moneda, primero la del activo: ARS 10000.00, USD 10.00",
         [("ARS", 1000000), ("USD", 1000)], [(p["moneda"], p["precio_promedio_minor"]) for p in e["precios_promedio"]])
    balance = {(b["tipo"], codigos.get(b["moneda_id"], "?")): b["saldo_neto_minor"] for b in svc.get_balance_por_tipo()}
    caso("get_balance_por_tipo(): CEDEARs en ARS = la compra en ARS", 10000000, balance.get(("cedear", "ARS")))
    caso("get_balance_por_tipo(): CEDEARs en USD = 50.00 − 35.00 + 2.00 (sin mezclar con ARS)",
         1700, balance.get(("cedear", "USD")))

    print("\n--- 4. FCI: la moneda es siempre la del activo ---")
    fci = svc.create_activo(nombre="FCI PESOS", tipo="fci", moneda_id=ars).entity_id
    aporte = svc.registrar_aporte(fci, 1000000, "2026-01-05", crear_transaccion=False).entity_id
    caso("aporte a un FCI en ARS: ARS", "ARS", moneda_de(aporte))
    antes = movimientos_totales()
    caso_excepcion("rendimiento en USD en un FCI en ARS → SavingsError", SavingsError,
                   lambda: svc.registrar_rendimiento(fci, 100, "2026-01-10", moneda_id=usd))
    caso("tras el rechazo no se escribió nada", antes, movimientos_totales())
    caso_excepcion("editar la moneda de un aporte de FCI → SavingsError", SavingsError,
                   lambda: svc.update_movement(aporte, moneda_id=usd))
    caso("tras el rechazo el aporte sigue en ARS", "ARS", moneda_de(aporte))
    caso("pedir la misma moneda del activo sí se acepta (sin cambios)", "SIN CAMBIOS.",
         svc.update_movement(aporte, moneda_id=ars).message)

    print("\n--- 5. update_movement(): cambiar la moneda de una compra de CEDEAR ---")
    svc.update_movement(compra_usd, moneda_id=ars)
    caso("la compra en USD pasó a ARS", "ARS", moneda_de(compra_usd))
    caso("precio promedio: ya no hay compras en USD (ARS = (100000.00 + 50.00) / 15)",
         [("ARS", 667000)], [(p["moneda"], p["precio_promedio_minor"]) for p in entrada(nvda)["precios_promedio"]])
    svc.update_movement(compra_usd, moneda_id=usd)
    caso("y de vuelta a USD", "USD", moneda_de(compra_usd))

    print("\n--- 6. Transacción vinculada: en la moneda del movimiento ---")
    cuenta = CuentasRepository(manager).crear(
        nombre="Broker (verify moneda)", tipo="debito", moneda_codigo="ARS", saldo_inicial=0.0,
    )
    accion = svc.create_activo(nombre="ACCION CON CUENTA", tipo="accion", moneda_id=ars, cuenta_id=cuenta).entity_id
    compra_vinculada = svc.registrar_compra(accion, 2, 2000, 0, "2026-01-09", moneda_id=usd)
    transaccion_id = compra_vinculada.data["transaccion_id"]
    caso("la compra creó su transacción del Registro", True, transaccion_id is not None)
    transaccion = manager.fetchone("SELECT moneda_id, monto_minor FROM transacciones WHERE id = ?;", (transaccion_id,))
    caso("la transacción está en USD (la moneda del movimiento, no la del activo)", "USD", codigos.get(transaccion["moneda_id"]))
    caso("la transacción es por el total (20.00)", 2000, transaccion["monto_minor"])

    print("\n--- 7. Migración: backfill de moneda_id ---")
    migracion = next(m for m in MIGRACIONES_COLUMNA if m.tabla == "movimientos_activo" and m.columna == "moneda_id")
    fci_usd = svc.create_activo(nombre="FCI DOLARES", tipo="fci", moneda_id=usd).entity_id
    sin_moneda = MovimientosActivoRepository(manager).crear(
        activo_id=fci_usd, tipo="aporte", fecha="2025-01-01", monto_total_minor=3800,
    )
    caso("una fila creada sin moneda queda NULL", None, fila(sin_moneda)["moneda_id"])
    caso("list_movimientos() igual la muestra en la moneda del activo (USD)", "USD",
         codigos.get(next(m for m in svc.list_movimientos(activo_id=fci_usd) if m["id"] == sin_moneda)["moneda_id"]))
    manager.conn.execute(migracion.sql_backfill)
    manager.conn.commit()
    caso("tras el backfill: la moneda del activo (USD)", "USD", moneda_de(sin_moneda))
    caso("el backfill no tocó las filas que ya tenían moneda", "USD", moneda_de(venta_usd))

    print(f"\n{casos_ok}/{casos_total} casos OK")
    print(f"(La DB temporal quedó en {db_path} — no es data/deltabalance.db.)")


if __name__ == "__main__":
    main()
