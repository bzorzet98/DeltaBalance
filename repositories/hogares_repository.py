"""
DeltaBalance — repositories/hogares_repository.py

Acceso a datos para la tabla `hogares`. Sin lógica de negocio: NO genera
`codigo_invitacion` — el propio docs/DATA_MODEL_DECISIONS.md sección 2 lo
documenta explícitamente ("la generación del código en sí es
responsabilidad de la capa de servicio, no del schema"). crear() solo
recibe un código ya generado por el caller y lo persiste.

Confirmado por grep en db/database.py (Fase 2, bloque HOGARES / GASTOS
COMPARTIDOS, paso 1): no existe ningún método relacionado con hogares/
hogar_miembros/gastos_compartidos — no hay comportamiento previo que
replicar, el diseño sale directo del schema.

`hogares` NO tiene columna `deleted_at` ni `activa` — por eso
obtener_por_id()/obtener_por_codigo() usan QueryBuilder con
include_deleted=True hardcodeado, mismo motivo que en los repositorios
anteriores sin esa columna.

crear() acepta `conn` desde el arranque: un hogar casi siempre se crea
junto con el alta de su primer HogarMiembro (quien lo crea), en la misma
operación atómica (ver HogarMiembrosRepository.agregar(conn=...)).
"""

import sqlite3
from typing import Optional

from db.database import DatabaseManager
from db.query_builder import QueryBuilder


class HogaresRepository:
    def __init__(self, db: DatabaseManager):
        self._db = db

    # ----------------------------------------------------------
    # CREATE
    # ----------------------------------------------------------

    def crear(
        self,
        codigo_invitacion: str,
        nombre: Optional[str] = None,
        conn: Optional[sqlite3.Connection] = None,
    ) -> int:
        """
        Inserta un hogar. codigo_invitacion ya viene generado por el
        caller (ver docstring del módulo) — UNIQUE en el schema, una
        colisión sube como sqlite3.IntegrityError tal cual, sin envolver
        (decidir cómo reaccionar es trabajo del futuro service).
        """
        sql = "INSERT INTO hogares (codigo_invitacion, nombre) VALUES (?, ?);"
        params = (codigo_invitacion, nombre)
        if conn is not None:
            return conn.execute(sql, params).lastrowid
        return self._db.execute(sql, params)

    # ----------------------------------------------------------
    # READ
    # ----------------------------------------------------------

    def obtener_por_id(self, hogar_id: int) -> Optional[sqlite3.Row]:
        return (
            QueryBuilder("hogares", include_deleted=True)
            .where("id", hogar_id)
            .ejecutar_uno(self._db.conn)
        )

    def obtener_por_codigo(self, codigo_invitacion: str) -> Optional[sqlite3.Row]:
        """Para cuando alguien se une a un hogar existente con el código de invitación."""
        return (
            QueryBuilder("hogares", include_deleted=True)
            .where("codigo_invitacion", codigo_invitacion)
            .ejecutar_uno(self._db.conn)
        )
