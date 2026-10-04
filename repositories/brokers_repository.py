"""
DeltaBalance — repositories/brokers_repository.py

Acceso a datos para la tabla `brokers` (db/schema.sql): dónde se opera un
activo financiero (COCOS, BULL MARKET, IOL…). Los iniciales los siembra
db/schema_migrations.py (BROKERS_INICIALES).

Sin lógica de negocio: no normaliza el nombre ni decide si un broker se
puede usar — eso vive en services/savings_service.py. `brokers` no tiene
`deleted_at` (tiene `activo`): SQL explícito, sin QueryBuilder.
"""

import sqlite3
from typing import Optional

from db.database import DatabaseManager
from repositories._ids import nuevo_id


class BrokersRepository:
    def __init__(self, db: DatabaseManager):
        self._db = db

    def crear(self, nombre: str, tipo: Optional[str] = None) -> str:
        """Inserta un broker y devuelve su id (UUID). El UNIQUE de nombre rechaza un duplicado (IntegrityError)."""
        broker_id = nuevo_id()
        self._db.conn.execute("INSERT INTO brokers (id, nombre, tipo) VALUES (?, ?, ?);", (broker_id, nombre, tipo))
        self._db.conn.commit()
        return broker_id

    def listar(self, solo_activos: bool = True) -> list[sqlite3.Row]:
        sql = "SELECT * FROM brokers" + (" WHERE activo = 1" if solo_activos else "") + " ORDER BY nombre;"
        return self._db.fetchall(sql)

    def obtener_por_id(self, broker_id: str) -> Optional[sqlite3.Row]:
        return self._db.fetchone("SELECT * FROM brokers WHERE id = ?;", (broker_id,))

    def obtener_por_nombre(self, nombre: str) -> Optional[sqlite3.Row]:
        return self._db.fetchone("SELECT * FROM brokers WHERE nombre = ?;", (nombre,))
