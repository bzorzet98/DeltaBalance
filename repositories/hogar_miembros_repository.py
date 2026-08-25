"""
DeltaBalance — repositories/hogar_miembros_repository.py

Acceso a datos para la tabla puente `hogar_miembros` (hogar <-> persona).
Sin lógica de negocio: agregar() NO envuelve el sqlite3.IntegrityError que
sube si (hogar_id, usuario_local) ya existe (PRIMARY KEY compuesta) —
decidir qué hacer ante "esa persona ya es miembro de ese hogar" es
responsabilidad del futuro service, no de este repositorio.

Confirmado por grep en db/database.py (Fase 2, bloque HOGARES / GASTOS
COMPARTIDOS, paso 1): no existe ningún método relacionado.

`hogar_miembros` no tiene columna `id` propia (PK compuesta
hogar_id+usuario_local) ni `deleted_at`/`activa` — QueryBuilder no
necesita una columna `id` para funcionar (filtra por cualquier columna),
así que las lecturas sí pasan por QueryBuilder con include_deleted=True
hardcodeado, mismo motivo que en los repositorios anteriores sin esa
columna.

agregar()/actualizar_porcentaje_default() aceptan `conn` desde el
arranque: un alta de miembro casi siempre participa de la misma
transacción que crea el hogar (ver HogaresRepository.crear(conn=...)).
"""

import sqlite3
from typing import Optional

from db.database import DatabaseManager
from db.query_builder import QueryBuilder


class HogarMiembrosRepository:
    def __init__(self, db: DatabaseManager):
        self._db = db

    # ----------------------------------------------------------
    # CREATE
    # ----------------------------------------------------------

    def agregar(
        self,
        hogar_id: int,
        usuario_local: str,
        porcentaje_default: Optional[float] = None,
        conn: Optional[sqlite3.Connection] = None,
    ) -> None:
        """
        Inserta un miembro en un hogar. Sin id propio que devolver (PK
        compuesta). Si (hogar_id, usuario_local) ya existe, sube
        sqlite3.IntegrityError tal cual — no se envuelve (ver docstring
        del módulo).
        """
        sql = """
            INSERT INTO hogar_miembros (hogar_id, usuario_local, porcentaje_default)
            VALUES (?, ?, ?);
        """
        params = (hogar_id, usuario_local, porcentaje_default)
        if conn is not None:
            conn.execute(sql, params)
            return
        self._db.execute(sql, params)

    # ----------------------------------------------------------
    # READ
    # ----------------------------------------------------------

    def listar_miembros(self, hogar_id: int) -> list[sqlite3.Row]:
        return (
            QueryBuilder("hogar_miembros", include_deleted=True)
            .where("hogar_id", hogar_id)
            .order("usuario_local")
            .ejecutar(self._db.conn)
        )

    def listar_hogares_de_usuario(self, usuario_local: str) -> list[sqlite3.Row]:
        """
        Filas de hogar_miembros (hogar_id, usuario_local, porcentaje_default)
        donde usuario_local es miembro. Agregado para que
        SharedExpensesService.list_my_hogares() pueda armar "mis hogares" —
        antes solo se consultaba en la dirección hogar->miembros
        (listar_miembros()), nunca en la dirección usuario->hogares.
        """
        return (
            QueryBuilder("hogar_miembros", include_deleted=True)
            .where("usuario_local", usuario_local)
            .order("hogar_id")
            .ejecutar(self._db.conn)
        )

    def obtener_miembro(self, hogar_id: int, usuario_local: str) -> Optional[sqlite3.Row]:
        return (
            QueryBuilder("hogar_miembros", include_deleted=True)
            .where("hogar_id", hogar_id)
            .where("usuario_local", usuario_local)
            .ejecutar_uno(self._db.conn)
        )

    # ----------------------------------------------------------
    # UPDATE
    # ----------------------------------------------------------

    def actualizar_porcentaje_default(
        self,
        hogar_id: int,
        usuario_local: str,
        porcentaje_default: Optional[float],
        conn: Optional[sqlite3.Connection] = None,
    ) -> None:
        """
        Escribe porcentaje_default reemplazando el valor existente por
        completo — no es un NO_CAMBIAR condicional, siempre escribe lo que
        se le pasa, incluido None para quitar el porcentaje (mismo
        criterio que DeudasRepository.write_off() con `notas`).
        """
        sql = """
            UPDATE hogar_miembros SET porcentaje_default = ?
            WHERE hogar_id = ? AND usuario_local = ?;
        """
        params = (porcentaje_default, hogar_id, usuario_local)
        if conn is not None:
            conn.execute(sql, params)
            return
        self._db.execute(sql, params)
