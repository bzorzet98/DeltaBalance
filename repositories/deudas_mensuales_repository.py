"""
DeltaBalance — repositories/deudas_mensuales_repository.py

Acceso a datos para la tabla `deudas_mensuales` (snapshot, al cierre de un
mes, del saldo con cada (persona, tab, moneda) — tabla creada vía
db/schema_migrations.py MIGRACIONES_TABLA; `tab` separa 'me_deben' de
'debo', igual que en `deudas`). Sin lógica de negocio: cómo se normaliza el
nombre de la persona, el signo de monto_minor y para qué meses se guarda lo
decide services/snapshots_service.py.

upsert() usa INSERT OR REPLACE sobre el UNIQUE(entidad_persona, tab,
moneda_id, mes, anio). Sin `deleted_at`: QueryBuilder con
include_deleted=True. Las escrituras aceptan `conn` para participar de la
transacción del service.
"""

import sqlite3
from typing import Optional

from db.database import DatabaseManager
from db.query_builder import QueryBuilder


class DeudasMensualesRepository:
    def __init__(self, db: DatabaseManager):
        self._db = db

    # ----------------------------------------------------------
    # UPSERT
    # ----------------------------------------------------------

    def upsert(
        self,
        entidad_persona: str,
        tab: str,
        moneda_id: int,
        mes: int,
        anio: int,
        monto_minor: int,
        conn: Optional[sqlite3.Connection] = None,
    ) -> None:
        sql = """
            INSERT OR REPLACE INTO deudas_mensuales (entidad_persona, tab, moneda_id, mes, anio, monto_minor)
            VALUES (?, ?, ?, ?, ?, ?);
        """
        params = (entidad_persona, tab, moneda_id, mes, anio, monto_minor)
        if conn is not None:
            conn.execute(sql, params)
            return
        self._db.execute(sql, params)

    # ----------------------------------------------------------
    # READ
    # ----------------------------------------------------------

    def obtener(self, entidad_persona: str, tab: str, moneda_id: int, mes: int, anio: int) -> Optional[sqlite3.Row]:
        return (
            QueryBuilder("deudas_mensuales", include_deleted=True)
            .where("entidad_persona", entidad_persona)
            .where("tab", tab)
            .where("moneda_id", moneda_id)
            .where("mes", mes)
            .where("anio", anio)
            .ejecutar_uno(self._db.conn)
        )

    def listar_por_periodo(self, mes: int, anio: int, tab: Optional[str] = None) -> list[sqlite3.Row]:
        """Los snapshots de un mes (una fila por (persona, tab, moneda)), de un tab o de los dos (tab=None)."""
        return (
            QueryBuilder("deudas_mensuales", include_deleted=True)
            .where("mes", mes)
            .where("anio", anio)
            .where("tab", tab)
            .order("entidad_persona")
            .order("moneda_id")
            .ejecutar(self._db.conn)
        )

    # ----------------------------------------------------------
    # DELETE
    # ----------------------------------------------------------

    def eliminar_desde(self, mes: int, anio: int, conn: Optional[sqlite3.Connection] = None) -> None:
        """DELETE físico de todos los snapshots de mes/anio en adelante (para recalcular desde ahí)."""
        sql = "DELETE FROM deudas_mensuales WHERE anio > ? OR (anio = ? AND mes >= ?);"
        params = (anio, anio, mes)
        if conn is not None:
            conn.execute(sql, params)
            return
        self._db.execute(sql, params)
