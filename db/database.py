"""
DeltaBalance — db/database.py

Clase de conexión a la base de datos SQLite: apertura/cierre, transacciones,
inicialización (schema + seed en una base nueva, migraciones versionadas por
PRAGMA user_version en una existente — db/schema_migrations.py), backup, y las
utilidades crudas (fetchall/fetchone/execute) que usan repositories/ y
services/ para ejecutar SQL directo cuando hace falta.

Esta clase YA NO es un god-class de CRUD por entidad — ese código se migró
a repositories/ (uno por tabla/agregado) a lo largo de la Fase 2, bloque por
bloque (ver CLAUDE.md §1 y docs/ARCHITECTURE.md). Lo que queda acá es
exclusivamente: conexión/transacciones/inicialización, y un puñado de
métodos de lectura (monedas, tipo de cambio, vistas agregadas) que nunca
tuvieron un repositorio propio porque no están cubiertos por ningún bloque
de la Fase 2 — quedan como utilidad de bajo nivel, no como precedente para
código nuevo (para una entidad nueva, el patrón correcto sigue siendo un
repositorio dedicado).
"""

import logging
import os
import sqlite3
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional
from contextlib import contextmanager

from db import schema_migrations

logger = logging.getLogger(__name__)

# =============================================================
# RUTAS POR DEFECTO
# =============================================================
BASE_DIR    = Path(__file__).resolve().parent.parent
DATA_DIR    = BASE_DIR / "data"
SCHEMA_PATH = BASE_DIR / "db" / "schema.sql"
SEED_PATH   = BASE_DIR / "db" / "seed.sql"
DB_PATH     = DATA_DIR / "deltabalance.db"

# App empaquetada (flet build): la base va en la carpeta de datos de la app.
VARIABLE_DATOS_APP = "FLET_APP_STORAGE_DATA"  # la define Flet
NOMBRE_DB_APP      = "deltabalance.db"


def get_db_path(app_dir: Optional[str] = None) -> Path:
    """
    Ruta de la base según cómo corre la app:

    - app_dir explícito → <app_dir>/deltabalance.db.
    - App empaquetada (flet build: Linux/Windows/macOS, Android, iOS):
      Flet define FLET_APP_STORAGE_DATA con la carpeta de datos de la app,
      que se conserva al actualizarla → <esa carpeta>/deltabalance.db. La
      primera vez está vacía: se copia una .db ahí o se restaura desde
      Supabase. (Flet 0.86.5 no tiene page.app_dir: por eso se lee la
      variable, que además no necesita la página.)
    - Desde el código (python main.py o flet run): DB_PATH
      (data/deltabalanceBZ.db), como siempre. `flet run` también define
      FLET_APP_STORAGE_DATA, pero dentro del proyecto (.flet/storage/data):
      ahí se ignora — si no, la base de desarrollo pasaría a una vacía.
    """
    if app_dir:
        return Path(app_dir) / NOMBRE_DB_APP
    datos_app = os.environ.get(VARIABLE_DATOS_APP)
    if datos_app and not Path(datos_app).resolve().is_relative_to(BASE_DIR):
        return Path(datos_app) / NOMBRE_DB_APP
    return DB_PATH


# =============================================================
# HELPERS: conversión minor units ↔ float
# =============================================================

def to_minor(amount: float, decimals: int = 2) -> int:
    """Convierte un monto float a minor units. Ej: 1234.56 ARS → 123456"""
    return round(amount * (10 ** decimals))


def from_minor(minor: int, decimals: int = 2) -> float:
    """Convierte minor units a float. Ej: 123456 → 1234.56"""
    return minor / (10 ** decimals)


# =============================================================
# DATABASE MANAGER
# =============================================================

class DatabaseManager:
    """
    Conexión, transacciones e inicialización de la base de datos SQLite de
    DeltaBalance. El CRUD por entidad vive en repositories/, no acá.

    Uso típico:
        db = DatabaseManager()
        db.inicializar()
        repo = CuentasRepository(db)
        cuentas = repo.listar()
    """

    def __init__(self, db_path: Path = DB_PATH):
        self.db_path     = Path(db_path)
        self.schema_path = SCHEMA_PATH
        self.seed_path   = SEED_PATH
        self._conn: Optional[sqlite3.Connection] = None

    # ----------------------------------------------------------
    # CONEXIÓN
    # ----------------------------------------------------------
    @contextmanager
    def transaction(self):
        conn = self.conn
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    def conectar(self) -> sqlite3.Connection:
        """Abre (o reutiliza) la conexión a la base de datos."""
        if self._conn is None:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(self.db_path)
            self._conn.row_factory = sqlite3.Row      # acceso por nombre de columna
            self._conn.execute("PRAGMA foreign_keys = ON;")
            self._conn.execute("PRAGMA journal_mode = WAL;")
        return self._conn

    def desconectar(self):
        """Cierra la conexión si está abierta."""
        if self._conn:
            self._conn.close()
            self._conn = None

    def __enter__(self):
        self.conectar()
        return self

    def __exit__(self, *_):
        self.desconectar()

    @property
    def conn(self) -> sqlite3.Connection:
        return self.conectar()

    # ----------------------------------------------------------
    # INICIALIZACIÓN
    # ----------------------------------------------------------

    def inicializar(self, force: bool = False):
        """
        Deja la base lista para usar, según su PRAGMA user_version
        (db/schema_migrations.py, docs/DATA_MODEL_DECISIONS.md sección 37):

        - Base nueva (sin tablas: el archivo no existía, o existe vacío):
          schema.sql — la foto de la versión actual —, brokers iniciales,
          sync, seed.sql (solo si el archivo no existía: un archivo creado
          vacío a propósito, como el destino de
          legacy_migration/migrar_a_uuid_pk.py, queda sin seed) y, al final,
          user_version = VERSION_SCHEMA.
        - Base existente con user_version menor: backup del archivo y
          después las migraciones que falten (run_migrations(), que marca la
          versión al terminar cada una).
        - Base existente al día: ninguna migración.
        - En todos los casos, preparar_sync() (fuera del versionado).

        Si force=True, elimina la DB existente primero.

        Raises:
            RuntimeError si la base es de una versión más nueva que este
            código, si tiene ids enteros o si una migración falla (la
            versión queda en la última que terminó bien).
        """
        if force:
            self.eliminar_db()

        ya_existia = self.db_path.exists()
        conn = self.conectar()
        nueva = schema_migrations.base_vacia(conn)
        version = schema_migrations.leer_version(conn)

        if nueva:
            with open(self.schema_path, "r", encoding="utf-8") as f:
                conn.executescript(f.read())
            # Antes de preparar_sync(), como en la cadena anterior: los
            # brokers de una base nueva no quedan anotados en sync_cambios.
            schema_migrations.sembrar_brokers(conn)
        elif version > schema_migrations.VERSION_SCHEMA:
            raise RuntimeError(
                f"La base de datos está en la versión de schema {version} y esta versión de la app "
                f"llega hasta la {schema_migrations.VERSION_SCHEMA}. Actualizá la app antes de abrirla."
            )
        elif version < schema_migrations.VERSION_SCHEMA:
            self._backup_antes_de_migrar(version)
            schema_migrations.run_migrations(conn, version)

        # Fuera del versionado, en cada arranque: columna sincronizado_en,
        # tablas de control y los triggers trg_sync_* con su definición
        # actual.
        schema_migrations.preparar_sync(conn)

        if nueva:
            # El seed va después de preparar_sync(), como antes del
            # versionado: sus filas (categorías, "Caja Efectivo") quedan
            # anotadas en sync_cambios por los triggers.
            if not ya_existia:
                with open(self.seed_path, "r", encoding="utf-8") as f:
                    conn.executescript(f.read())
            # La marca, al final: solo una base completa queda en la versión actual.
            schema_migrations.marcar_version(conn, schema_migrations.VERSION_SCHEMA)
            logger.info("Base de datos inicializada en: %s", self.db_path)
        else:
            logger.debug("Base de datos ya existente en: %s", self.db_path)

    def _backup_antes_de_migrar(self, version: int) -> Path:
        """
        hacer_backup() antes de migrar una base existente:
        <nombre>_backup_antes_migracion_v<version>_<fecha>_<hora>.db, al lado
        del archivo. hacer_backup() copia solo el archivo principal: antes se
        pasa todo el -wal a él (checkpoint) — si no se puede pasar completo
        (otra conexión leyendo), no se migra, porque el backup quedaría sin
        los últimos cambios.
        """
        conn = self.conn
        conn.commit()
        ocupada, paginas_wal, paginas_copiadas = conn.execute("PRAGMA wal_checkpoint(TRUNCATE);").fetchone()
        if ocupada or (paginas_wal != -1 and paginas_copiadas != paginas_wal):
            raise RuntimeError(
                "No se pudo hacer el backup previo a la migración de la base: hay otra conexión abierta "
                "sobre el archivo. Cerrá las otras instancias de la app y volvé a abrirla."
            )
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        destino = self.db_path.parent / f"{self.db_path.stem}_backup_antes_migracion_v{version}_{ts}.db"
        return self.hacer_backup(destino)

    def esta_inicializada(self) -> bool:
        """Verifica si la DB existe y tiene las tablas principales."""
        if not self.db_path.exists():
            return False
        try:
            cur = self.conn.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='transacciones';"
            )
            return cur.fetchone()[0] > 0
        except sqlite3.DatabaseError:
            return False

    # ----------------------------------------------------------
    # BACKUP Y ELIMINACIÓN
    # ----------------------------------------------------------

    def hacer_backup(self, destino: Optional[Path] = None) -> Path:
        """
        Copia la DB a un archivo de backup con timestamp.
        Devuelve la ruta del backup generado.
        """
        if not self.db_path.exists():
            raise FileNotFoundError("No existe base de datos para respaldar.")

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        destino = destino or self.db_path.parent / f"deltabalance_backup_{ts}.db"
        shutil.copy2(self.db_path, destino)
        logger.info("Backup creado en: %s", destino)
        return destino

    def eliminar_db(self):
        """Elimina el archivo de base de datos. Usar con cuidado."""
        self.desconectar()
        if self.db_path.exists():
            self.db_path.unlink()
            logger.info("Base de datos eliminada: %s", self.db_path)

    # ----------------------------------------------------------
    # UTILIDAD INTERNA
    # ----------------------------------------------------------

    def fetchall(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        return self.conn.execute(sql, params).fetchall()

    def fetchone(self, sql: str, params: tuple = ()) -> Optional[sqlite3.Row]:
        return self.conn.execute(sql, params).fetchone()

    def execute(self, sql: str, params: tuple = (),
                autocommit: bool = True) -> int:
        """Ejecuta INSERT/UPDATE/DELETE. Devuelve el lastrowid o rowcount."""
        if params is not None:
            assert sql.count("?") == len(params), (
                f"\nSQL placeholder mismatch\n"
                f"Expected: {sql.count('?')}\n"
                f"Received: {len(params)}\n\n"
                f"Query:\n{sql}\n\n"
                f"Params:\n{params}"
            )
        cur = self.conn.execute(sql, params)
        if autocommit:
            self.conn.commit()
        return cur.lastrowid or cur.rowcount

    # ===========================================================
    # MONEDAS
    # ===========================================================
    # No hay MonedasRepository (ningún bloque de la Fase 2 lo creó todavía —
    # "monedas" no es un dominio con su propio paso de migración). Estos tres
    # métodos quedan acá sin caller real hoy (repositories/ y services/
    # consultan la tabla `monedas` con SQL directo vía self._db.fetchone(),
    # no llaman a estos métodos) — se dejan de todos modos porque no están
    # cubiertos por ningún repositorio, así que no califican para esta
    # limpieza (que solo borra lo ya migrado). Si en algún momento se crea
    # MonedasRepository, esto se deprecia igual que el resto.

    def obtener_monedas(self) -> list[sqlite3.Row]:
        return self.fetchall("SELECT * FROM monedas ORDER BY codigo;")

    def obtener_moneda_por_codigo(self, codigo: str) -> Optional[sqlite3.Row]:
        return self.fetchone("SELECT * FROM monedas WHERE codigo = ?;", (codigo,))

    # obtener_moneda_id() se eliminó en v3: el código ES la clave de la
    # moneda (docs/DATA_MODEL_DECISIONS.md sección 39).

    # (TIPOS DE CAMBIO: obtener_tipo_cambio() se eliminó junto con la tabla
    # tipos_cambio en la migración v2 — docs/DATA_MODEL_DECISIONS.md sección
    # 38. No tenía caller.)

    # ===========================================================
    # VISTAS / PROYECCIONES
    # ===========================================================
    # resumen_mensual()/balance_todas_las_cuentas() no tienen caller real
    # confirmado hoy (TransactionService.monthly_summary() ya es el
    # reemplazo moderno de resumen_mensual, pero database.py no lo sabe ni
    # lo necesita saber) — no están cubiertos por ningún repositorio, así
    # que quedan fuera de esta limpieza igual que MONEDAS arriba. proyeccion_tarjeta_proximos_meses() SÍ se eliminó: dependía
    # de obtener_cuotas_por_mes() (bloque COMPRAS_CUOTAS, ya migrado a
    # CuotasCreditoRepository.listar_por_mes() y borrado en esta misma
    # limpieza) y tampoco tenía caller real — dejarla habría dejado un
    # método roto (llamando a un método que ya no existe).

    def resumen_mensual(self, mes: int, anio: int) -> list[sqlite3.Row]:
        """Ingresos y egresos reales del mes, agrupados por moneda."""
        return self.fetchall(
            """
            SELECT
                m.codigo                                                        AS moneda,
                SUM(CASE WHEN t.tipo_movimiento = 'ingreso'
                         THEN t.monto_minor ELSE 0 END)                        AS ingresos_minor,
                SUM(CASE WHEN t.tipo_movimiento = 'egreso'
                         THEN t.monto_minor ELSE 0 END)                        AS egresos_minor,
                SUM(CASE WHEN t.tipo_movimiento = 'ingreso'
                         THEN t.monto_minor ELSE -t.monto_minor END)           AS balance_minor
            FROM transacciones t
            JOIN monedas m ON m.codigo = t.moneda_codigo
            WHERE strftime('%m', t.fecha) = printf('%02d', ?)
              AND strftime('%Y', t.fecha) = CAST(? AS TEXT)
            GROUP BY m.codigo;
            """,
            (mes, anio),
        )

    def balance_todas_las_cuentas(self) -> list[sqlite3.Row]:
        """Devuelve el saldo actual de todas las cuentas activas."""
        return self.fetchall("SELECT * FROM vw_balance_cuentas ORDER BY nombre;")

    # ===========================================================
    # INFORMACIÓN Y DIAGNÓSTICO
    # ===========================================================

    def info_db(self) -> dict:
        """Devuelve estadísticas básicas de la base de datos."""
        tablas = [
            "cuentas", "transacciones", "compras_cuotas",
            "cuotas_credito", "deudas", "presupuestos", "recibos_sueldo",
        ]
        info = {"path": str(self.db_path), "tablas": {}}
        for tabla in tablas:
            row = self.fetchone(f"SELECT COUNT(*) AS n FROM {tabla};")
            info["tablas"][tabla] = row["n"] if row else 0
        return info
