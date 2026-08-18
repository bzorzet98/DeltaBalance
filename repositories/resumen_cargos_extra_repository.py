"""
DeltaBalance — repositories/resumen_cargos_extra_repository.py

Acceso a datos para la tabla `resumen_cargos_extra`. Sin lógica de negocio:
no decide qué cargos corresponden a un resumen, no calcula
porcentaje_impuesto_bp (eso es responsabilidad de quien orqueste el cierre
de un resumen — ver ResumenesTarjetaRepository.marcar_cerrado()).

`resumen_cargos_extra` NO tiene `deleted_at` ni `activa`: son datos de apoyo
al cierre de un resumen, editables libremente hasta que el resumen se
cierra (ver docs/DATA_MODEL_DECISIONS.md sección 12) — por eso eliminar()
hace un DELETE físico, no soft-delete. No es un registro contable
independiente con historial propio (a diferencia de deuda_pagos), así que
no aplica la regla de ventana de corrección temprana de CLAUDE.md sección 4
de la misma forma: acá no hay "estado propio generado" que bloquee el
borrado, todo cargo extra vive y muere junto con el ciclo de vida del
resumen al que pertenece.
"""

import sqlite3
from typing import Optional

from db.database import DatabaseManager


class ResumenCargosExtraRepository:
    def __init__(self, db: DatabaseManager):
        self._db = db

    # ----------------------------------------------------------
    # CREATE
    # ----------------------------------------------------------

    def agregar(
        self,
        resumen_id: int,
        concepto: str,
        tipo: str,
        monto_minor: int,
        conn: Optional[sqlite3.Connection] = None,
    ) -> int:
        """
        Inserta un cargo extra de un resumen. monto_minor puede ser
        negativo (ej. un ajuste a favor del usuario).
        """
        sql = """
            INSERT INTO resumen_cargos_extra (resumen_id, concepto, tipo, monto_minor)
            VALUES (?, ?, ?, ?);
        """
        params = (resumen_id, concepto, tipo, monto_minor)
        if conn is not None:
            return conn.execute(sql, params).lastrowid
        return self._db.execute(sql, params)

    # ----------------------------------------------------------
    # READ
    # ----------------------------------------------------------

    def obtener_por_id(self, id: int) -> Optional[sqlite3.Row]:
        return self._db.fetchone("SELECT * FROM resumen_cargos_extra WHERE id = ?;", (id,))

    def listar_por_resumen(
        self,
        resumen_id: int,
        conn: Optional[sqlite3.Connection] = None,
    ) -> list[sqlite3.Row]:
        sql = "SELECT * FROM resumen_cargos_extra WHERE resumen_id = ? ORDER BY id ASC;"
        if conn is not None:
            return conn.execute(sql, (resumen_id,)).fetchall()
        return self._db.fetchall(sql, (resumen_id,))

    def suma_por_resumen(
        self,
        resumen_id: int,
        conn: Optional[sqlite3.Connection] = None,
    ) -> int:
        """
        Suma neta de monto_minor de todos los cargos de un resumen (incluye
        negativos). Devuelve 0 si no hay ningún cargo — COALESCE evita que
        SUM() sobre un conjunto vacío devuelva None.

        Si se pasa `conn`, la lectura se hace sobre esa conexión (para ver
        INSERTs no comiteados de una transacción externa genuinamente
        distinta a self._db.conn — ver ResumenesTarjetaRepository.marcar_cerrado()).
        Si no, lee de self._db.conn como siempre.
        """
        sql = "SELECT COALESCE(SUM(monto_minor), 0) AS total FROM resumen_cargos_extra WHERE resumen_id = ?;"
        if conn is not None:
            fila = conn.execute(sql, (resumen_id,)).fetchone()
        else:
            fila = self._db.fetchone(sql, (resumen_id,))
        return fila["total"]

    # ----------------------------------------------------------
    # DELETE
    # ----------------------------------------------------------

    def eliminar(self, id: int, conn: Optional[sqlite3.Connection] = None) -> None:
        """DELETE físico — ver docstring del módulo."""
        sql = "DELETE FROM resumen_cargos_extra WHERE id = ?;"
        if conn is not None:
            conn.execute(sql, (id,))
            return
        self._db.execute(sql, (id,))
