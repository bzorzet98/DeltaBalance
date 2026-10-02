"""
DeltaBalance — repositories/saldos_mensuales_repository.py

Acceso a datos para la tabla `saldos_mensuales` (snapshot del saldo de cada
(cuenta, moneda) al cierre de un mes — tabla creada vía
db/schema_migrations.py MIGRACIONES_TABLA). Sin lógica de negocio: qué se
guarda, para qué meses y cómo se calcula lo decide
services/snapshots_service.py.

upsert() usa INSERT OR REPLACE sobre el UNIQUE(cuenta_id, moneda_id, mes,
anio): recalcular un mes pisa el snapshot anterior (fila nueva, con
calculado_en actualizado por el DEFAULT).

La tabla no tiene `deleted_at` (es un caché recalculable): QueryBuilder con
include_deleted=True, mismo criterio que los repositorios de tablas sin esa
columna. Las escrituras aceptan `conn` para participar de la transacción
del service (un recálculo completo es una sola transacción).
"""

import sqlite3
from typing import Optional

from db.database import DatabaseManager
from db.query_builder import QueryBuilder


class SaldosMensualesRepository:
    def __init__(self, db: DatabaseManager):
        self._db = db

    # ----------------------------------------------------------
    # UPSERT
    # ----------------------------------------------------------

    def upsert(
        self,
        cuenta_id: str,
        moneda_id: int,
        mes: int,
        anio: int,
        saldo_minor: int,
        conn: Optional[sqlite3.Connection] = None,
    ) -> None:
        sql = """
            INSERT OR REPLACE INTO saldos_mensuales (cuenta_id, moneda_id, mes, anio, saldo_minor)
            VALUES (?, ?, ?, ?, ?);
        """
        params = (cuenta_id, moneda_id, mes, anio, saldo_minor)
        if conn is not None:
            conn.execute(sql, params)
            return
        self._db.execute(sql, params)

    # ----------------------------------------------------------
    # READ
    # ----------------------------------------------------------

    def obtener(self, cuenta_id: str, moneda_id: int, mes: int, anio: int) -> Optional[sqlite3.Row]:
        return (
            QueryBuilder("saldos_mensuales", include_deleted=True)
            .where("cuenta_id", cuenta_id)
            .where("moneda_id", moneda_id)
            .where("mes", mes)
            .where("anio", anio)
            .ejecutar_uno(self._db.conn)
        )

    def listar_por_cuenta(self, cuenta_id: str, moneda_id: int) -> list[sqlite3.Row]:
        """Todos los snapshots de una (cuenta, moneda), del más viejo al más nuevo."""
        return (
            QueryBuilder("saldos_mensuales", include_deleted=True)
            .where("cuenta_id", cuenta_id)
            .where("moneda_id", moneda_id)
            .order("anio")
            .order("mes")
            .ejecutar(self._db.conn)
        )

    def listar_por_periodo(self, mes: int, anio: int) -> list[sqlite3.Row]:
        """Todos los snapshots de un mes (una fila por (cuenta, moneda))."""
        return (
            QueryBuilder("saldos_mensuales", include_deleted=True)
            .where("mes", mes)
            .where("anio", anio)
            .order("cuenta_id")
            .order("moneda_id")
            .ejecutar(self._db.conn)
        )

    # ----------------------------------------------------------
    # DELETE
    # ----------------------------------------------------------

    def eliminar_desde(self, mes: int, anio: int, conn: Optional[sqlite3.Connection] = None) -> None:
        """DELETE físico de todos los snapshots de mes/anio en adelante (para recalcular desde ahí)."""
        sql = "DELETE FROM saldos_mensuales WHERE anio > ? OR (anio = ? AND mes >= ?);"
        params = (anio, anio, mes)
        if conn is not None:
            conn.execute(sql, params)
            return
        self._db.execute(sql, params)
