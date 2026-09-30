"""
DeltaBalance — repositories/tarjetas_config_repository.py

Acceso a datos para la tabla `tarjetas_config` (días de cierre y de
vencimiento de cada tarjeta de crédito — tabla creada vía
db/schema_migrations.py MIGRACIONES_TABLA, docs/DATA_MODEL_DECISIONS.md
sección 23). Una fila por tarjeta (UNIQUE cuenta_id).

Sin lógica de negocio: no valida que la cuenta sea una tarjeta ni el rango
de los días (el CHECK de la tabla es la última red), y no calcula fechas de
resumen — eso vive en services/fees_service.py.

upsert() usa INSERT … ON CONFLICT(cuenta_id) DO UPDATE (no INSERT OR
REPLACE): así la fila conserva su id y su creada_en, y updated_en se
escribe acá en cada cambio (la tabla no tiene trigger).
"""

import sqlite3
from typing import Optional

from db.database import DatabaseManager


class TarjetasConfigRepository:
    def __init__(self, db: DatabaseManager):
        self._db = db

    # ----------------------------------------------------------
    # UPSERT
    # ----------------------------------------------------------

    def upsert(
        self,
        cuenta_id: int,
        dia_cierre: int,
        dia_vencimiento: int,
        conn: Optional[sqlite3.Connection] = None,
    ) -> None:
        sql = """
            INSERT INTO tarjetas_config (cuenta_id, dia_cierre, dia_vencimiento)
            VALUES (?, ?, ?)
            ON CONFLICT(cuenta_id) DO UPDATE SET
                dia_cierre      = excluded.dia_cierre,
                dia_vencimiento = excluded.dia_vencimiento,
                updated_en      = CURRENT_TIMESTAMP;
        """
        params = (cuenta_id, dia_cierre, dia_vencimiento)
        if conn is not None:
            conn.execute(sql, params)
            return
        self._db.execute(sql, params)

    # ----------------------------------------------------------
    # READ
    # ----------------------------------------------------------

    def obtener(self, cuenta_id: int) -> Optional[sqlite3.Row]:
        return self._db.fetchone("SELECT * FROM tarjetas_config WHERE cuenta_id = ?;", (cuenta_id,))

    def listar(self) -> list[sqlite3.Row]:
        """Todas las configuraciones, por cuenta_id."""
        return self._db.fetchall("SELECT * FROM tarjetas_config ORDER BY cuenta_id;")
