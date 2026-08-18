"""
DeltaBalance — db/database.py

Clase de conexión a la base de datos SQLite: apertura/cierre, transacciones,
inicialización (schema + seed + migraciones de columna), backup, y las
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

import sqlite3
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional
from contextlib import contextmanager

from db import schema_migrations



# =============================================================
# RUTAS POR DEFECTO
# =============================================================
BASE_DIR    = Path(__file__).resolve().parent.parent
DATA_DIR    = BASE_DIR / "data"
SCHEMA_PATH = BASE_DIR / "db" / "schema.sql"
SEED_PATH   = BASE_DIR / "db" / "seed.sql"
DB_PATH     = DATA_DIR / "deltabalance.db"


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
        Crea la base de datos aplicando schema.sql y seed.sql.
        Si force=True, elimina la DB existente primero.
        """
        if force:
            self.eliminar_db()

        ya_existia = self.db_path.exists()
        conn = self.conectar()

        # Aplicar schema
        with open(self.schema_path, "r", encoding="utf-8") as f:
            conn.executescript(f.read())

        # Migraciones de columna sobre tablas existentes (ver db/schema_migrations.py)
        schema_migrations.aplicar_migraciones_columna(conn)

        # Aplicar seed solo si la DB era nueva
        if not ya_existia:
            with open(self.seed_path, "r", encoding="utf-8") as f:
                conn.executescript(f.read())
            print(f"[DeltaBalance] Base de datos inicializada en: {self.db_path}")
        else:
            print(f"[DeltaBalance] Base de datos ya existente en: {self.db_path}")

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
        print(f"[DeltaBalance] Backup creado en: {destino}")
        return destino

    def eliminar_db(self):
        """Elimina el archivo de base de datos. Usar con cuidado."""
        self.desconectar()
        if self.db_path.exists():
            self.db_path.unlink()
            print(f"[DeltaBalance] Base de datos eliminada: {self.db_path}")

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

    def obtener_moneda_id(self, codigo: str) -> int:
        row = self.obtener_moneda_por_codigo(codigo)
        if row is None:
            raise ValueError(f"Moneda no encontrada: {codigo}")
        return row["id"]

    # ===========================================================
    # TIPOS DE CAMBIO
    # ===========================================================
    # No hay TiposCambioRepository. obtener_tipo_cambio() no tiene caller
    # real confirmado hoy tampoco, pero por el mismo motivo que MONEDAS
    # arriba — no está cubierto por ningún repositorio — queda fuera de
    # esta limpieza. registrar_tipo_cambio() SÍ se elimina acá: es uno de
    # los 3 métodos sin caller identificados en la auditoría de rowcount
    # (docs/AUDITORIA_EXECUTE_ROWCOUNT.md) que ya estaba decidido que se
    # iban en esta limpieza.

    def obtener_tipo_cambio(
        self, fecha: str, moneda_origen: str, moneda_destino: str = "ARS"
    ) -> Optional[float]:
        """Devuelve la tasa de cambio para una fecha dada, o None si no existe."""
        origen_id  = self.obtener_moneda_id(moneda_origen)
        destino_id = self.obtener_moneda_id(moneda_destino)
        row = self.fetchone(
            """
            SELECT tasa_minor FROM tipos_cambio
            WHERE fecha <= ? AND moneda_origen_id = ? AND moneda_destino_id = ?
            ORDER BY fecha DESC LIMIT 1;
            """,
            (fecha, origen_id, destino_id),
        )
        return row["tasa_minor"] / 10000 if row else None

    # ===========================================================
    # VISTAS / PROYECCIONES
    # ===========================================================
    # resumen_mensual()/balance_todas_las_cuentas() no tienen caller real
    # confirmado hoy (TransactionService.monthly_summary() ya es el
    # reemplazo moderno de resumen_mensual, pero database.py no lo sabe ni
    # lo necesita saber) — no están cubiertos por ningún repositorio, así
    # que quedan fuera de esta limpieza igual que MONEDAS/TIPOS DE CAMBIO
    # arriba. proyeccion_tarjeta_proximos_meses() SÍ se eliminó: dependía
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
            JOIN monedas m ON m.id = t.moneda_id
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
