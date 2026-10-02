"""
DeltaBalance — repositories/compartidos_mensuales_repository.py

Acceso a datos para la tabla `compartidos_mensuales` (snapshot, al cierre
de un mes, de lo pendiente en gastos compartidos por (hogar, pagador,
moneda) — tabla creada vía db/schema_migrations.py MIGRACIONES_TABLA). Sin
lógica de negocio: de dónde sale la moneda de cada gasto y para qué meses
se guarda lo decide services/snapshots_service.py.

upsert() usa INSERT OR REPLACE sobre el UNIQUE(hogar_id, pagador,
moneda_id, mes, anio). Sin `deleted_at`: QueryBuilder con
include_deleted=True. Las escrituras aceptan `conn` para participar de la
transacción del service.

eliminar_desde(): hogar_id=None borra los de TODOS los hogares (lo que
necesita un recálculo completo); con un hogar_id, solo los de ese hogar.
"""

import sqlite3
from typing import Optional

from db.database import DatabaseManager
from db.query_builder import QueryBuilder


class CompartidosMensualesRepository:
    def __init__(self, db: DatabaseManager):
        self._db = db

    # ----------------------------------------------------------
    # UPSERT
    # ----------------------------------------------------------

    def upsert(
        self,
        hogar_id: str,
        pagador: str,
        moneda_id: int,
        mes: int,
        anio: int,
        monto_minor: int,
        conn: Optional[sqlite3.Connection] = None,
    ) -> None:
        sql = """
            INSERT OR REPLACE INTO compartidos_mensuales (hogar_id, pagador, moneda_id, mes, anio, monto_minor)
            VALUES (?, ?, ?, ?, ?, ?);
        """
        params = (hogar_id, pagador, moneda_id, mes, anio, monto_minor)
        if conn is not None:
            conn.execute(sql, params)
            return
        self._db.execute(sql, params)

    # ----------------------------------------------------------
    # READ
    # ----------------------------------------------------------

    def obtener(
        self, hogar_id: str, pagador: str, moneda_id: int, mes: int, anio: int,
    ) -> Optional[sqlite3.Row]:
        return (
            QueryBuilder("compartidos_mensuales", include_deleted=True)
            .where("hogar_id", hogar_id)
            .where("pagador", pagador)
            .where("moneda_id", moneda_id)
            .where("mes", mes)
            .where("anio", anio)
            .ejecutar_uno(self._db.conn)
        )

    def listar_por_hogar_periodo(self, hogar_id: str, mes: int, anio: int) -> list[sqlite3.Row]:
        """Todos los snapshots de un hogar en un mes (una fila por (pagador, moneda))."""
        return (
            QueryBuilder("compartidos_mensuales", include_deleted=True)
            .where("hogar_id", hogar_id)
            .where("mes", mes)
            .where("anio", anio)
            .order("pagador")
            .order("moneda_id")
            .ejecutar(self._db.conn)
        )

    # ----------------------------------------------------------
    # DELETE
    # ----------------------------------------------------------

    def eliminar_desde(
        self, hogar_id: Optional[str], mes: int, anio: int, conn: Optional[sqlite3.Connection] = None,
    ) -> None:
        """DELETE físico de los snapshots de mes/anio en adelante — de un hogar, o de todos con hogar_id=None."""
        sql = "DELETE FROM compartidos_mensuales WHERE (anio > ? OR (anio = ? AND mes >= ?))"
        params: tuple = (anio, anio, mes)
        if hogar_id is not None:
            sql += " AND hogar_id = ?"
            params += (hogar_id,)
        sql += ";"
        if conn is not None:
            conn.execute(sql, params)
            return
        self._db.execute(sql, params)
