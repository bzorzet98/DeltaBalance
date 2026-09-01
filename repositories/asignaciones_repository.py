"""
DeltaBalance — repositories/asignaciones_repository.py

Acceso a datos para la tabla `asignaciones` (puente movimientos_activo <->
objetivos_ahorro). Sin lógica de negocio: NO valida que la suma de
porcentaje de un mismo movimiento_id no supere 100% — el propio schema.sql
lo documenta explícitamente (ver comentario arriba de `CREATE TABLE
asignaciones` en db/schema.sql): un CHECK de columna no puede ver el resto
de las filas de la tabla, así que esa validación es responsabilidad
exclusiva de un futuro SavingsService (Fase 2, bloque AHORROS, paso 2),
que debe llamar a suma_porcentaje_por_movimiento() ANTES de insertar/
actualizar acá y decidir si rechaza la operación.

Confirmado por grep en db/database.py (Fase 2, bloque AHORROS, paso 1): no
existe ningún método relacionado — no hay comportamiento previo que
replicar.

crear() acepta `conn` desde el arranque: una asignación inicial casi
siempre se registra en la misma operación atómica que el
MovimientosActivoRepository.crear() del movimiento al que pertenece (ver
docstring de ese módulo y el verify de este).

`asignaciones` NO tiene columna `deleted_at` — por eso los métodos de
lectura usan QueryBuilder con include_deleted=True hardcodeado, mismo
motivo que en los repositorios anteriores sin esa columna. No hay
actualizar(): una asignación no tiene ningún campo mutable documentado por
la consigna de esta tarea (solo crear/listar/sumar/eliminar) — si en el
futuro hace falta editar una asignación, se agrega ese método cuando haya
un caso real que lo pida, no antes.
"""

import sqlite3
from typing import Optional

from db.database import DatabaseManager
from db.query_builder import QueryBuilder


class AsignacionesRepository:
    def __init__(self, db: DatabaseManager):
        self._db = db

    # ----------------------------------------------------------
    # CREATE
    # ----------------------------------------------------------

    def crear(
        self,
        movimiento_id: int,
        objetivo_id: int,
        porcentaje: float,
        monto_asignado_minor: int,
        conn: Optional[sqlite3.Connection] = None,
    ) -> int:
        """
        Inserta una asignación. UNIQUE(movimiento_id, objetivo_id) en el
        schema — no se puede asignar el mismo movimiento al mismo objetivo
        dos veces (habría que actualizar la fila existente, no insertar
        otra).

        Si se pasa `conn`, participa de la transacción externa que
        también inserta el movimiento_activo al que pertenece esta
        asignación (ver MovimientosActivoRepository.crear(conn=...)).
        """
        sql = """
            INSERT INTO asignaciones (movimiento_id, objetivo_id, porcentaje, monto_asignado_minor)
            VALUES (?, ?, ?, ?);
        """
        params = (movimiento_id, objetivo_id, porcentaje, monto_asignado_minor)
        if conn is not None:
            return conn.execute(sql, params).lastrowid
        return self._db.execute(sql, params)

    # ----------------------------------------------------------
    # READ
    # ----------------------------------------------------------

    def listar_por_movimiento(self, movimiento_id: int) -> list[sqlite3.Row]:
        return (
            QueryBuilder("asignaciones", include_deleted=True)
            .where("movimiento_id", movimiento_id)
            .ejecutar(self._db.conn)
        )

    def listar_por_objetivo(self, objetivo_id: int) -> list[sqlite3.Row]:
        return (
            QueryBuilder("asignaciones", include_deleted=True)
            .where("objetivo_id", objetivo_id)
            .ejecutar(self._db.conn)
        )

    def suma_porcentaje_por_movimiento(self, movimiento_id: int) -> float:
        """
        Suma de `porcentaje` de todas las asignaciones de un movimiento.
        Devuelve 0.0 si no hay ninguna — COALESCE evita que SUM() sobre un
        conjunto vacío devuelva None. Pensado para que un futuro
        SavingsService valide "no superar 100%" ANTES de llamar a crear()
        — este método no rechaza nada por sí mismo, solo informa.
        """
        fila = self._db.fetchone(
            "SELECT COALESCE(SUM(porcentaje), 0.0) AS total FROM asignaciones WHERE movimiento_id = ?;",
            (movimiento_id,),
        )
        return fila["total"]

    # ----------------------------------------------------------
    # DELETE
    # ----------------------------------------------------------

    def eliminar(self, id: int) -> None:
        """DELETE físico — una asignación es un dato de apoyo al reparto, sin historial propio."""
        self._db.execute("DELETE FROM asignaciones WHERE id = ?;", (id,))

    def eliminar_por_movimiento(
        self, movimiento_id: int, conn: Optional[sqlite3.Connection] = None,
    ) -> None:
        """
        DELETE físico de TODAS las asignaciones de un movimiento_id de una
        sola vez — para cuando se borra el movimiento entero (ver
        MovimientosActivoRepository.eliminar()), no un borrado selectivo de
        una asignación puntual (eso sigue siendo eliminar(id)).

        Si se pasa `conn`, participa de la transacción externa que también
        borra el movimiento_activo dueño de estas asignaciones.
        """
        sql = "DELETE FROM asignaciones WHERE movimiento_id = ?;"
        if conn is not None:
            conn.execute(sql, (movimiento_id,))
            return
        self._db.execute(sql, (movimiento_id,))
