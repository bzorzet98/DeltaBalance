"""
verify/sync/verify_ahorros_sync.py

Verifica que Ahorros se sincronice (pedido del usuario: lo cargado con
`python main.py` no aparecía en la app empaquetada, que usa otra base):
brokers, activos_financieros, objetivos_ahorro, movimientos_activo,
asignaciones y activo_objetivos en TABLAS_SINCRONIZADAS
(db/schema_migrations.py), y TABLAS NUEVAS de sync/sync_engine.py.

Escenario, con dos bases temporales del MISMO usuario y un Supabase falso
en memoria (verify/sync/_fake_supabase.py):
  1. VERSIÓN ANTERIOR (las tablas de Ahorros fuera de TABLAS_SINCRONIZADAS,
     emulado sacándolas de la lista): DESARROLLO carga un FCI en COCOS con
     un aporte asignado a un objetivo; las dos bases sincronizan — viajan
     la cuenta y la transacción del aporte, Ahorros no.
  2. VERSIÓN NUEVA (la lista completa e inicializar(), como al abrir la app
     actualizada): DESARROLLO sincroniza y sube Ahorros.
  3. EMPAQUETADA sincroniza con su marca de bajada MÁS ADELANTE que lo que
     subió DESARROLLO (el caso que la marca sola se saltearía):
     - baja Ahorros entero igual (las tablas nuevas se bajan desde cero);
     - su broker COCOS — sembrado en cada base con su propio UUID — adopta
       el id del de DESARROLLO (clave natural), así el activo bajado apunta
       a un broker que existe y en Supabase no queda un COCOS duplicado;
     - una segunda sync no reaplica nada.
  4. Al revés: un objetivo creado en EMPAQUETADA llega a DESARROLLO.

Correlo con:
    python verify/sync/verify_ahorros_sync.py
"""

from pathlib import Path
import json
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from verify._dummy_db import crear_dummy_db
from verify.sync._fake_supabase import FakeSupabase, MarcasEnMemoria
from db.database import DatabaseManager
from db.schema_migrations import TABLAS_SINCRONIZADAS
from services.savings_service import SavingsService
from sync.sync_engine import PREFIJO_TABLA_BAJADA, SyncEngine

BRUNO = "f5ca8a42-a039-4a46-998e-2b01dbb28e9c"
MARCA_EN_EL_FUTURO = "9999-12-31T00:00:00+00:00"
TABLAS_AHORROS = (
    "brokers", "activos_financieros", "objetivos_ahorro", "movimientos_activo", "asignaciones", "activo_objetivos",
)


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

    fake = FakeSupabase()

    def base(nombre: str) -> DatabaseManager:
        path = crear_dummy_db()
        print(f"Dummy DB ({nombre}): {path}")
        manager = DatabaseManager(db_path=path)
        manager.inicializar()
        return manager

    def broker_id(m: DatabaseManager, nombre: str) -> str:
        return m.fetchone("SELECT id FROM brokers WHERE nombre = ?;", (nombre,))["id"]

    def contar(m: DatabaseManager, tabla: str) -> int:
        return m.fetchone(f"SELECT COUNT(*) AS n FROM {tabla};")["n"]

    def remotas(tabla: str) -> list[dict]:
        """`datos` de las filas privadas de esa tabla en Supabase."""
        return [
            json.loads(f["datos"]) if isinstance(f["datos"], str) else f["datos"]
            for f in fake.tablas["deltabalance_filas"].values() if f.get("tabla") == tabla
        ]

    lista_completa = list(TABLAS_SINCRONIZADAS)
    try:
        # ============================================================
        print("--- 1. Versión anterior: Ahorros todavía no se sincroniza ---")
        # ============================================================
        TABLAS_SINCRONIZADAS[:] = [t for t in lista_completa if t not in TABLAS_AHORROS]
        md = base("DESARROLLO")
        me = base("EMPAQUETADA")
        cocos_d, cocos_e = broker_id(md, "COCOS"), broker_id(me, "COCOS")
        caso("cada base sembró su broker COCOS con su propio UUID", True, cocos_d != cocos_e)

        ahorros_d = SavingsService(md)
        ars = md.fetchone("SELECT id FROM monedas WHERE codigo = 'ARS';")["id"]
        caja = md.fetchone("SELECT id FROM cuentas WHERE nombre = 'Caja Efectivo';")["id"]
        objetivo = ahorros_d.create_objetivo("VIAJE", 100000000).entity_id
        activo = ahorros_d.create_activo("FCI COCOS", "fci", ars, cuenta_id=caja, broker_id=cocos_d).entity_id
        aporte = ahorros_d.registrar_aporte(
            activo, 5000000, "2026-10-01", asignaciones=[{"objetivo_id": objetivo, "porcentaje": 100}],
        )
        movimiento = aporte.entity_id

        marcas_d, marcas_e = MarcasEnMemoria(), MarcasEnMemoria()
        motor_d = SyncEngine(md, None, cliente=fake, usuario_id=BRUNO, marcas=marcas_d)
        motor_e = SyncEngine(me, None, cliente=fake, usuario_id=BRUNO, marcas=marcas_e)
        caso("DESARROLLO: sync (versión anterior) OK", True, motor_d.sync_completo().success)
        caso("EMPAQUETADA: sync (versión anterior) OK", True, motor_e.sync_completo().success)
        caso("… Ahorros no viajó: EMPAQUETADA no tiene el activo", 0,
             me.fetchone("SELECT COUNT(*) AS n FROM activos_financieros WHERE id = ?;", (activo,))["n"])
        tx_aporte = md.fetchone("SELECT transaccion_id FROM movimientos_activo WHERE id = ?;", (movimiento,))["transaccion_id"]
        caso("… la transacción del aporte sí (transacciones ya se sincronizaba)", True,
             me.fetchone("SELECT id FROM transacciones WHERE id = ?;", (tx_aporte,)) is not None)

        # ============================================================
        print("\n--- 2. Versión nueva: DESARROLLO sube Ahorros ---")
        # ============================================================
        TABLAS_SINCRONIZADAS[:] = lista_completa
        md.inicializar()  # al abrir la app actualizada: sincronizado_en y triggers en las tablas nuevas
        me.inicializar()
        r = motor_d.sync_completo()
        caso("DESARROLLO: sync sin errores", (True, 0), (r.success, r.errores))
        caso("en Supabase: el activo, con su broker", cocos_d,
             next((d.get("broker_id") for d in remotas("activos_financieros") if d.get("id") == activo), None))
        caso("… el movimiento, su asignación y el objetivo", (True, True, True),
             (any(d.get("id") == movimiento for d in remotas("movimientos_activo")),
              any(d.get("movimiento_id") == movimiento for d in remotas("asignaciones")),
              any(d.get("id") == objetivo for d in remotas("objetivos_ahorro"))))
        caso("DESARROLLO anotó que ya bajó las tablas de Ahorros enteras", True,
             all(md.fetchone("SELECT 1 FROM sync_estado WHERE clave = ?;", (PREFIJO_TABLA_BAJADA + t,)) is not None
                 for t in TABLAS_AHORROS))

        # ============================================================
        print("\n--- 3. EMPAQUETADA, con la marca de bajada más adelante que lo subido ---")
        # ============================================================
        for clave in marcas_e.marcas:
            marcas_e.marcas[clave] = MARCA_EN_EL_FUTURO
        r = motor_e.sync_completo()
        caso("EMPAQUETADA: sync sin errores", (True, 0), (r.success, r.errores))
        print(f"   mensaje: {r.mensaje}")
        caso("bajó el activo de DESARROLLO (las tablas nuevas se bajan desde cero, no desde la marca)", True,
             me.fetchone("SELECT id FROM activos_financieros WHERE id = ?;", (activo,)) is not None)
        caso("su COCOS adoptó el UUID del de DESARROLLO (clave natural)", cocos_d, broker_id(me, "COCOS"))
        caso("… un solo COCOS en EMPAQUETADA", 1, me.fetchone("SELECT COUNT(*) AS n FROM brokers WHERE nombre = 'COCOS';")["n"])
        caso("… y el activo apunta a él", cocos_d,
             me.fetchone("SELECT broker_id FROM activos_financieros WHERE id = ?;", (activo,))["broker_id"])
        caso("en Supabase no quedó un COCOS duplicado", 1,
             sum(1 for d in remotas("brokers") if d.get("nombre") == "COCOS" and not d.get("_borrado")))
        caso("bajaron el movimiento (con su transacción) y la asignación", (tx_aporte, 1),
             (me.fetchone("SELECT transaccion_id FROM movimientos_activo WHERE id = ?;", (movimiento,))["transaccion_id"],
              me.fetchone("SELECT COUNT(*) AS n FROM asignaciones WHERE movimiento_id = ?;", (movimiento,))["n"]))
        ahorros_e = SavingsService(me)
        caso("la pantalla de Ahorros de EMPAQUETADA ve el activo y el objetivo", (["FCI COCOS"], ["VIAJE"]),
             ([a["nombre"] for a in ahorros_e.list_activos()], [o["nombre"] for o in ahorros_e.list_objetivos()]))
        caso("… y el objetivo tiene el aporte asignado", 5000000, ahorros_e.get_objetivo_balance(objetivo)["invertido_minor"])
        caso("mismas filas de Ahorros en las dos bases",
             {t: contar(md, t) for t in TABLAS_AHORROS}, {t: contar(me, t) for t in TABLAS_AHORROS})
        r = motor_e.sync_completo()
        caso("otra sync de EMPAQUETADA sin cambios: (subidas, bajadas, errores) = 0", (0, 0, 0),
             (r.subidas, r.bajadas, r.errores))

        # ============================================================
        print("\n--- 4. Al revés: de EMPAQUETADA a DESARROLLO ---")
        # ============================================================
        casa = ahorros_e.create_objetivo("CASA").entity_id
        motor_e.sync_completo()
        r = motor_d.sync_completo()
        caso("DESARROLLO: sync sin errores", 0, r.errores)
        caso("el objetivo creado en EMPAQUETADA llegó a DESARROLLO", True,
             md.fetchone("SELECT id FROM objetivos_ahorro WHERE id = ?;", (casa,)) is not None)
    finally:
        TABLAS_SINCRONIZADAS[:] = lista_completa

    md.desconectar()
    me.desconectar()
    print(f"\n{casos_ok}/{casos_total} casos OK")


if __name__ == "__main__":
    main()
