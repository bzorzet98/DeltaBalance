"""
DeltaBalance — repositories/movimientos_activo_repository.py

Acceso a datos para la tabla `movimientos_activo`. Sin lógica de negocio:
no calcula cantidad/precio_unitario_minor a partir de nada, no decide si un
rendimiento "corresponde" repartirse entre asignaciones — eso vive en un
futuro SavingsService (Fase 2, bloque AHORROS, paso 2, todavía no existe).

Confirmado por grep en db/database.py (Fase 2, bloque AHORROS, paso 1): no
existe ningún método relacionado — no hay comportamiento previo que
replicar.

`movimientos_activo` es un registro de movimiento append-only (mismo rol
que `deuda_pagos`): no tiene `updated_en`, no tiene `deleted_at`, y no hay
ningún método de update en este repositorio — un movimiento no se edita,
si está mal cargado se corrige con un movimiento nuevo (o se borra si
todavía no generó dependencias, decisión de un futuro service, no de
este repositorio).

crear() acepta `conn` desde el arranque: un movimiento de tipo 'compra'
casi siempre se registra junto con su(s) asignacion(es) inicial(es) a un
objetivo de ahorro en la misma operación atómica (ver
AsignacionesRepository.crear(conn=...) y el verify de este módulo).

`transaccion_id` (columna agregada vía db/schema_migrations.py, Fase 2,
bloque AHORROS, Tarea 6b de docs/PROXIMOS_PASOS.md): vínculo opcional a la
transacción real que descontó/acreditó una cuenta cuando el movimiento de
ahorro no fue puramente informal — mismo patrón que
`recibos_sueldo.transaccion_id`. Nullable: sigue en NULL para movimientos
sin cuenta asociada (comportamiento previo, sin cambios). Orquestar CUÁNDO
se llena es responsabilidad de SavingsService, no de este repositorio.
"""

import sqlite3
from typing import Optional

from db.database import DatabaseManager
from db.query_builder import QueryBuilder


class MovimientosActivoRepository:
    def __init__(self, db: DatabaseManager):
        self._db = db

    # ----------------------------------------------------------
    # CREATE
    # ----------------------------------------------------------

    def crear(
        self,
        activo_id: int,
        tipo: str,
        fecha: str,
        monto_total_minor: int,
        cantidad: Optional[float] = None,
        precio_unitario_minor: Optional[int] = None,
        dolar_oficial_momento_minor: Optional[int] = None,
        notas: Optional[str] = None,
        transaccion_id: Optional[int] = None,
        conn: Optional[sqlite3.Connection] = None,
    ) -> int:
        """
        Inserta un movimiento de activo (compra/venta/rendimiento).

        transaccion_id: opcional, ver docstring del módulo. NULL si el
        movimiento es puramente informal (comportamiento previo, sin
        cambios).

        Si se pasa `conn`, el INSERT se ejecuta ahí directamente sin
        comitear, para participar de la transacción externa que también
        inserta la(s) asignación(es) inicial(es) (ver docstring del
        módulo). Si no se pasa, comportamiento standalone normal vía
        self._db.execute().
        """
        sql = """
            INSERT INTO movimientos_activo
                (activo_id, tipo, fecha, cantidad, precio_unitario_minor,
                 monto_total_minor, dolar_oficial_momento_minor, notas,
                 transaccion_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
        """
        params = (
            activo_id, tipo, fecha, cantidad, precio_unitario_minor,
            monto_total_minor, dolar_oficial_momento_minor, notas,
            transaccion_id,
        )
        if conn is not None:
            return conn.execute(sql, params).lastrowid
        return self._db.execute(sql, params)

    # ----------------------------------------------------------
    # READ
    # ----------------------------------------------------------

    def obtener_por_id(self, movimiento_id: int) -> Optional[sqlite3.Row]:
        return (
            QueryBuilder("movimientos_activo", include_deleted=True)
            .where("id", movimiento_id)
            .ejecutar_uno(self._db.conn)
        )

    def listar_por_activo(self, activo_id: int) -> list[sqlite3.Row]:
        return (
            QueryBuilder("movimientos_activo", include_deleted=True)
            .where("activo_id", activo_id)
            .order("fecha")
            .ejecutar(self._db.conn)
        )

    def listar_por_tipo(self, activo_id: int, tipo: str) -> list[sqlite3.Row]:
        return (
            QueryBuilder("movimientos_activo", include_deleted=True)
            .where("activo_id", activo_id)
            .where("tipo", tipo)
            .order("fecha")
            .ejecutar(self._db.conn)
        )
