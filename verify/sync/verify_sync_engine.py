"""
verify/sync/verify_sync_engine.py

Verifica la sincronización (sync/sync_engine.py + repositories/
sync_repository.py + los triggers de db/schema_migrations.py
preparar_sync(), docs/DATA_MODEL_DECISIONS.md sección 24) SIN red: contra
DBs temporales y un Supabase FALSO en memoria (FakeSupabase, abajo) que
implementa solo lo que usa el motor — table().select/eq/in_/gt/order/
range/limit/upsert().execute() — con subido_en como un reloj del
"servidor" que siempre avanza. No prueba RLS ni la red real: eso se
prueba con la app contra Supabase.

Cubre:
  - inicializar(): sincronizado_en en las 15 tablas, sync_cambios /
    sync_estado, 45 triggers; los triggers registran altas, ediciones y
    borrados, y las filas anteriores a los triggers quedan pendientes por
    sincronizado_en NULL.
  - Primera sincronización (compu 1): sube todo; después no queda nada
    pendiente — marcar como sincronizada no vuelve a marcarla (ni el
    trigger de updated_en de transacciones); la marca de sync_estado
    nunca queda comiteada.
  - Una edición y un borrado locales viajan (el borrado, como borrado=True).
  - Restauración en otra compu (compu 2, base nueva): baja lo propio con
    los mismos ids, lo remoto gana sobre el seed, y lo borrado no vuelve.
  - Conflicto: gana la edición más nueva (last-write-wins), en los dos
    sentidos.
  - sync_fila() sube solo esa fila.
  - Sin conexión: success=False, estado SIN CONEXIÓN, lo pendiente sigue
    pendiente. Los oyentes se enteran de cada cambio de estado.

Correlo con:
    python verify/sync/verify_sync_engine.py
"""

import copy
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from verify._dummy_db import crear_dummy_db
from db.database import DatabaseManager
from db.schema_migrations import TABLAS_SINCRONIZADAS
from services.accounts_service import AccountsService
from services.debts_service import DebtsService
from services.transaction_service import TransactionService
from sync.sync_engine import ESTADO_SIN_CONEXION, ESTADO_SINCRONIZADO, TABLA_REMOTA, SyncEngine

BRUNO = "f5ca8a42-a039-4a46-998e-2b01dbb28e9c"


# ============================================================
# Supabase falso (solo lo que usa SyncEngine)
# ============================================================

class _Respuesta:
    def __init__(self, data: list, count=None):
        self.data = data
        self.count = count


class FakeSupabase:
    def __init__(self) -> None:
        self.tablas: dict[str, dict[tuple, dict]] = {"deltabalance_filas": {}, "deltabalance_hogar_miembros": {}}
        self._reloj = 0
        self.caido = False

    def ahora(self) -> str:
        self._reloj += 1
        return f"2026-10-01T00:00:00.{self._reloj:06d}+00:00"

    def table(self, nombre: str) -> "_Consulta":
        if self.caido:
            raise ConnectionError("sin red (simulado)")
        return _Consulta(self, nombre)

    def remota(self, tabla: str, clave: str) -> dict:
        return self.tablas[TABLA_REMOTA].get((BRUNO, tabla, clave), {})


class _Consulta:
    def __init__(self, fake: FakeSupabase, tabla: str):
        self._fake, self._tabla = fake, tabla
        self._filtros: list = []
        self._orden: list[str] = []
        self._rango = None
        self._upsert = None
        self._contar = False

    def select(self, *columnas, count=None):
        self._contar = count is not None
        return self

    def eq(self, columna, valor):
        self._filtros.append(lambda f: f.get(columna) == valor)
        return self

    def in_(self, columna, valores):
        conjunto = set(valores)
        self._filtros.append(lambda f: f.get(columna) in conjunto)
        return self

    def gt(self, columna, valor):
        self._filtros.append(lambda f: (f.get(columna) or "") > valor)
        return self

    def order(self, columna, desc=False):
        self._orden.append(columna)
        return self

    def range(self, inicio, fin):
        self._rango = (inicio, fin)
        return self

    def limit(self, cantidad):
        self._rango = (0, cantidad - 1)
        return self

    def upsert(self, filas, on_conflict=""):
        self._upsert = (filas, on_conflict.split(","))
        return self

    def execute(self) -> _Respuesta:
        almacen = self._fake.tablas[self._tabla]
        if self._upsert is not None:
            filas, claves = self._upsert
            devueltas = []
            for fila in filas:
                clave = tuple(fila[c] for c in claves)
                # deepcopy: como un ida y vuelta por JSON.
                nueva = {**almacen.get(clave, {}), **copy.deepcopy(fila), "subido_en": self._fake.ahora()}
                almacen[clave] = nueva
                devueltas.append(copy.deepcopy(nueva))
            return _Respuesta(devueltas)
        filas = [copy.deepcopy(f) for f in almacen.values() if all(filtro(f) for filtro in self._filtros)]
        for columna in reversed(self._orden):
            filas.sort(key=lambda f: str(f.get(columna) or ""))
        total = len(filas)
        if self._rango is not None:
            filas = filas[self._rango[0]:self._rango[1] + 1]
        return _Respuesta(filas, count=total if self._contar else None)


class MarcasEnMemoria:
    """Reemplazo de MarcasEnPrefs: el verify no toca .deltabalance_prefs.json."""

    def __init__(self) -> None:
        self.marcas: dict[str, str] = {}

    def leer(self, usuario_id):
        return self.marcas.get(usuario_id)

    def escribir(self, usuario_id, valor):
        self.marcas[usuario_id] = valor


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

    def pendientes(manager: DatabaseManager, tabla: str = None) -> int:
        if tabla is None:
            return manager.fetchone("SELECT COUNT(*) AS n FROM sync_cambios;")["n"]
        return manager.fetchone("SELECT COUNT(*) AS n FROM sync_cambios WHERE tabla = ?;", (tabla,))["n"]

    fake = FakeSupabase()

    # ============================================================
    print("--- inicializar(): columnas, tablas de control y triggers ---")
    # ============================================================
    db_1 = crear_dummy_db()
    print(f"Dummy DB (compu 1): {db_1}\n")
    m1 = DatabaseManager(db_path=db_1)
    m1.inicializar()
    sin_columna = [
        t for t in TABLAS_SINCRONIZADAS
        if "sincronizado_en" not in {f["name"] for f in m1.fetchall(f"PRAGMA table_info({t});")}
    ]
    caso("las 15 tablas sincronizadas tienen sincronizado_en", [], sin_columna)
    tablas = {f["name"] for f in m1.fetchall("SELECT name FROM sqlite_master WHERE type = 'table';")}
    caso("existen sync_cambios y sync_estado", True, {"sync_cambios", "sync_estado"} <= tablas)
    triggers = m1.fetchone("SELECT COUNT(*) AS n FROM sqlite_master WHERE type = 'trigger' AND name LIKE 'trg_sync_%';")["n"]
    caso("3 triggers por tabla (45)", 3 * len(TABLAS_SINCRONIZADAS), triggers)
    m1.inicializar()
    caso("inicializar() dos veces no rompe", True, True)
    # crear_dummy_db() aplica el seed ANTES de inicializar(), sin triggers: esas
    # filas quedan pendientes por sincronizado_en NULL (la regla de las filas viejas).
    caso("las filas anteriores a los triggers (el seed) quedan pendientes: sincronizado_en NULL", True,
         m1.fetchone("SELECT COUNT(*) AS n FROM categorias WHERE sincronizado_en IS NULL;")["n"] > 0)

    cuentas_1 = AccountsService(m1)
    transacciones_1 = TransactionService(m1)
    deudas_1 = DebtsService(m1)
    ars = m1.fetchone("SELECT id FROM monedas WHERE codigo = 'ARS';")["id"]
    categoria = m1.fetchone("SELECT id FROM categorias WHERE tipo = 'egreso' ORDER BY id LIMIT 1;")["id"]
    cuenta = cuentas_1.create_account(nombre="Banco Sync", tipo="debito", monedas=[ars]).account_id
    tx = transacciones_1.create(
        date_str="2026-09-10", concept="Super", account_id=cuenta, category_id=categoria,
        currency_code="ARS", amount=1500.0, movement_type="egreso",
    ).transaction_id
    deuda = deudas_1.create("Noe", "Cena", "me_deben", 5000, ars, "2026-09-11").entity_id
    caso("el alta de una transacción queda pendiente", 1, pendientes(m1, "transacciones"))
    caso("la cuenta y su saldo (cuentas_saldos) también", True, pendientes(m1, "cuentas") > 0 and pendientes(m1, "cuentas_saldos") > 0)

    # ============================================================
    print("\n--- Primera sincronización (compu 1) ---")
    # ============================================================
    marcas_1 = MarcasEnMemoria()
    motor_1 = SyncEngine(m1, None, cliente=fake, usuario_id=BRUNO, marcas=marcas_1)
    avisos: list[str] = []
    motor_1.escuchar("verify", lambda motor: avisos.append(motor.estado))
    r = motor_1.sync_completo()
    caso("success", True, r.success)
    caso("subió filas", True, r.subidas > 0)
    caso("no quedó nada pendiente", 0, pendientes(m1))
    caso("… ni filas sin sincronizado_en en transacciones", 0,
         m1.fetchone("SELECT COUNT(*) AS n FROM transacciones WHERE sincronizado_en IS NULL;")["n"])
    caso("la marca de escritura de la sync no quedó comiteada", 0, m1.fetchone("SELECT COUNT(*) AS n FROM sync_estado;")["n"])
    caso("en Supabase está la transacción, con su concepto", "Super", fake.remota("transacciones", str(tx)).get("datos", {}).get("concepto"))
    caso("dejó la marca de bajada de la compu 1", True, marcas_1.leer(BRUNO) is not None)
    caso("los oyentes se enteraron (SINCRONIZANDO y después SINCRONIZADO)", ["sincronizando", "sincronizado"], avisos[-2:])
    r = motor_1.sync_completo()
    caso("otra sync sin cambios: 0 subidas, 0 bajadas (el eco de lo propio no se reaplica)", (0, 0), (r.subidas, r.bajadas))

    # ============================================================
    print("\n--- Una edición y un borrado viajan ---")
    # ============================================================
    transacciones_1.update(tx, concept="Super chino")
    caso("la edición quedó pendiente", 1, pendientes(m1, "transacciones"))
    deudas_1.delete(deuda)
    caso("el borrado quedó pendiente", "borrado",
         m1.fetchone("SELECT operacion FROM sync_cambios WHERE tabla = 'deudas';")["operacion"])
    r = motor_1.sync_completo()
    caso("subió las dos", 2, r.subidas)
    caso("en Supabase: el concepto nuevo", "Super chino", fake.remota("transacciones", str(tx)).get("datos", {}).get("concepto"))
    caso("en Supabase: la deuda marcada borrada", True, fake.remota("deudas", str(deuda)).get("borrado"))
    caso("no quedó nada pendiente", 0, pendientes(m1))

    # ============================================================
    print("\n--- Restauración en otra compu (base nueva) ---")
    # ============================================================
    db_2 = crear_dummy_db()
    print(f"Dummy DB (compu 2): {db_2}")
    m2 = DatabaseManager(db_path=db_2)
    m2.inicializar()
    marcas_2 = MarcasEnMemoria()
    motor_2 = SyncEngine(m2, None, cliente=fake, usuario_id=BRUNO, marcas=marcas_2)
    r = motor_2.sync_completo()
    caso("success", True, r.success)
    fila_tx = m2.fetchone("SELECT * FROM transacciones WHERE id = ?;", (tx,))
    caso("la transacción llegó con el mismo id y el concepto editado", "Super chino", fila_tx["concepto"] if fila_tx else None)
    caso("la cuenta llegó con su saldo", True,
         m2.fetchone("SELECT COUNT(*) AS n FROM cuentas_saldos WHERE cuenta_id = ?;", (cuenta,))["n"] == 1)
    caso("la deuda borrada no vuelve", None, m2.fetchone("SELECT id FROM deudas WHERE id = ?;", (deuda,)))
    caso("no quedó nada pendiente en la compu 2", 0, pendientes(m2))

    # ============================================================
    print("\n--- Conflicto: gana la edición más nueva ---")
    # ============================================================
    transacciones_2 = TransactionService(m2)
    transacciones_1.update(tx, concept="Editado en la compu 1")  # primero
    transacciones_2.update(tx, concept="Editado en la compu 2")  # después: más nueva
    motor_2.sync_completo()
    r = motor_1.sync_completo()
    caso("compu 1: hubo conflicto", True, r.conflictos >= 1)
    caso("compu 1: ganó la de la compu 2 (más nueva)", "Editado en la compu 2",
         m1.fetchone("SELECT concepto FROM transacciones WHERE id = ?;", (tx,))["concepto"])
    transacciones_1.update(tx, concept="De nuevo en la compu 1")
    motor_1.sync_completo()
    motor_2.sync_completo()
    caso("al revés: la compu 2 baja la edición nueva de la compu 1", "De nuevo en la compu 1",
         m2.fetchone("SELECT concepto FROM transacciones WHERE id = ?;", (tx,))["concepto"])

    # ============================================================
    print("\n--- sync_fila() ---")
    # ============================================================
    otra = transacciones_1.create(
        date_str="2026-09-12", concept="Kiosco", account_id=cuenta, category_id=categoria,
        currency_code="ARS", amount=300.0, movement_type="egreso",
    ).transaction_id
    transacciones_1.update(tx, concept="Pendiente todavía")
    r = motor_1.sync_fila("transacciones", otra)
    caso("sync_fila() sube solo esa fila", 1, r.subidas)
    caso("… la otra sigue pendiente", 1, pendientes(m1, "transacciones"))

    # ============================================================
    print("\n--- Sin conexión ---")
    # ============================================================
    fake.caido = True
    r = motor_1.sync_completo()
    caso("success=False", False, r.success)
    caso("estado SIN CONEXIÓN", ESTADO_SIN_CONEXION, motor_1.estado)
    caso("lo pendiente sigue pendiente", 1, pendientes(m1, "transacciones"))
    fake.caido = False
    r = motor_1.sync_completo()
    caso("vuelve la red: sube lo pendiente y estado SINCRONIZADO", (True, ESTADO_SINCRONIZADO), (r.success, motor_1.estado))

    m1.desconectar()
    m2.desconectar()
    print(f"\n{casos_ok}/{casos_total} casos OK")


if __name__ == "__main__":
    main()
