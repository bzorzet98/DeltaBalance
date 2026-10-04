"""
DeltaBalance — repositories/resumen_cargos_extra_repository.py

DEPRECADO (docs/DATA_MODEL_DECISIONS.md sección 28): los cargos extra viven
en compras_cuotas con es_cargo_extra = 1 — esta tabla no se sincroniza —, y
db/schema_migrations.py migrar_cargos_extra_a_compras() mueve las filas que
había. FeesService ya no escribe acá. Queda para: suma_por_resumen() en
ResumenesTarjetaRepository.marcar_cerrado() (las filas que no se pudieron
mover, tipo 'otro'), los scripts de verify/ que la prueban y bases viejas.
No sumar usos nuevos.

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

moneda_id y fecha: agregadas vía db/schema_migrations.py, NULL en los
cargos anteriores. Este repositorio las guarda y las devuelve tal cual; la
migración a compras_cuotas las usa (y deduce la moneda de los que no la
tienen).
"""

import sqlite3
from typing import Optional

from db.database import DatabaseManager
from repositories._ids import nuevo_id


class ResumenCargosExtraRepository:
    def __init__(self, db: DatabaseManager):
        self._db = db

    # ----------------------------------------------------------
    # CREATE
    # ----------------------------------------------------------

    def agregar(
        self,
        resumen_id: str,
        concepto: str,
        tipo: str,
        monto_minor: int,
        moneda_id: Optional[int] = None,
        fecha: Optional[str] = None,
        conn: Optional[sqlite3.Connection] = None,
    ) -> str:
        """
        Inserta un cargo extra de un resumen y devuelve su id (UUID,
        repositories/_ids.py). monto_minor puede ser
        negativo (ej. un ajuste a favor del usuario). moneda_id y fecha
        ('YYYY-MM-DD') son opcionales: None las deja en NULL.
        """
        cargo_id = nuevo_id()
        sql = """
            INSERT INTO resumen_cargos_extra (id, resumen_id, concepto, tipo, monto_minor, moneda_id, fecha)
            VALUES (?, ?, ?, ?, ?, ?, ?);
        """
        params = (cargo_id, resumen_id, concepto, tipo, monto_minor, moneda_id, fecha)
        if conn is not None:
            conn.execute(sql, params)
        else:
            self._db.execute(sql, params)
        return cargo_id

    # ----------------------------------------------------------
    # READ
    # ----------------------------------------------------------

    def obtener_por_id(self, id: str) -> Optional[sqlite3.Row]:
        return self._db.fetchone("SELECT * FROM resumen_cargos_extra WHERE id = ?;", (id,))

    def listar_por_resumen(
        self,
        resumen_id: str,
        conn: Optional[sqlite3.Connection] = None,
    ) -> list[sqlite3.Row]:
        # rowid = orden de alta (los ids son UUID: ordenar por id sería al azar).
        sql = "SELECT * FROM resumen_cargos_extra WHERE resumen_id = ? ORDER BY rowid ASC;"
        if conn is not None:
            return conn.execute(sql, (resumen_id,)).fetchall()
        return self._db.fetchall(sql, (resumen_id,))

    def suma_por_resumen(
        self,
        resumen_id: str,
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

    def eliminar(self, id: str, conn: Optional[sqlite3.Connection] = None) -> None:
        """DELETE físico — ver docstring del módulo."""
        sql = "DELETE FROM resumen_cargos_extra WHERE id = ?;"
        if conn is not None:
            conn.execute(sql, (id,))
            return
        self._db.execute(sql, (id,))
