"""
DeltaBalance — repositories/movimientos_activo_repository.py

Acceso a datos para la tabla `movimientos_activo`. Sin lógica de negocio:
no calcula cantidad/precio_unitario_minor a partir de nada, no decide si un
rendimiento "corresponde" repartirse entre asignaciones — eso vive en un
futuro SavingsService (Fase 2, bloque AHORROS, paso 2, todavía no existe).

Confirmado por grep en db/database.py (Fase 2, bloque AHORROS, paso 1): no
existe ningún método relacionado — no hay comportamiento previo que
replicar.

`movimientos_activo` es un registro de movimientos (mismo rol que la vieja
`deuda_pagos`): no tiene `updated_en` ni `deleted_at`. Hasta el rediseño
de Ahorros e Inversiones no tenía ningún update; ahora actualizar() existe
(pedido explícito) como acceso a datos, pero ningún service lo usa
todavía: si un movimiento puede editarse o no (sus asignaciones y su
transacción vinculada dependen de él) lo decide SavingsService.

`tipo` acepta 'compra', 'venta', 'rendimiento' y 'aporte' (CHECK ampliado
en db/schema_migrations.py); comision_minor es la comisión de ese
movimiento (columna de la misma migración).

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
from typing import Any, Optional

from db.database import DatabaseManager
from repositories._ids import nuevo_id
from db.query_builder import QueryBuilder

# Ver mismo mecanismo/motivo en repositories/transacciones_repository.py
# (VENTANA_DUPLICADO_SEGUNDOS / TransaccionDuplicadaError) — chequeo de
# seguridad contra doble-click/doble-Enter, no una regla de negocio.
VENTANA_DUPLICADO_SEGUNDOS = 5

# actualizar(): columnas que se pueden escribir (las claves de **kwargs van
# al SQL, así que nunca se acepta una que no esté acá).
COLUMNAS_EDITABLES = (
    "tipo", "fecha", "cantidad", "precio_unitario_minor", "monto_total_minor",
    "dolar_oficial_momento_minor", "comision_minor", "notas", "transaccion_id",
)


class MovimientoDuplicadoError(Exception):
    """
    Se lanza cuando crear() detecta un movimiento de activo con los mismos
    campos relevantes (activo_id, tipo, monto, fecha) insertado hace menos
    de VENTANA_DUPLICADO_SEGUNDOS. Ver TransaccionDuplicadaError en
    transacciones_repository.py — mismo criterio exacto.
    """


class MovimientosActivoRepository:
    def __init__(self, db: DatabaseManager):
        self._db = db

    def _existe_duplicado_reciente(
        self,
        activo_id: str,
        tipo: str,
        fecha: str,
        monto_total_minor: int,
        conn: Optional[sqlite3.Connection] = None,
    ) -> bool:
        sql = """
            SELECT 1 FROM movimientos_activo
            WHERE activo_id = ? AND tipo = ? AND fecha = ? AND monto_total_minor = ?
              AND (strftime('%s', 'now') - strftime('%s', creada_en)) < ?
            LIMIT 1;
        """
        params = (activo_id, tipo, fecha, monto_total_minor, VENTANA_DUPLICADO_SEGUNDOS)
        ejecutor = conn if conn is not None else self._db.conn
        return ejecutor.execute(sql, params).fetchone() is not None

    # ----------------------------------------------------------
    # CREATE
    # ----------------------------------------------------------

    def crear(
        self,
        activo_id: str,
        tipo: str,
        fecha: str,
        monto_total_minor: int,
        cantidad: Optional[float] = None,
        precio_unitario_minor: Optional[int] = None,
        dolar_oficial_momento_minor: Optional[int] = None,
        notas: Optional[str] = None,
        transaccion_id: Optional[str] = None,
        conn: Optional[sqlite3.Connection] = None,
        comision_minor: int = 0,
    ) -> str:
        """
        Inserta un movimiento de activo (compra/venta/rendimiento/aporte) y
        devuelve su id (UUID, repositories/_ids.py). comision_minor: la
        comisión de este movimiento (0 si no hubo).

        Antes del INSERT, rechaza la operación con MovimientoDuplicadoError
        si ya existe un movimiento con el mismo activo_id/tipo/fecha/
        monto_total_minor creado hace menos de VENTANA_DUPLICADO_SEGUNDOS.

        transaccion_id: opcional, ver docstring del módulo. NULL si el
        movimiento es puramente informal (comportamiento previo, sin
        cambios).

        Si se pasa `conn`, el INSERT se ejecuta ahí directamente sin
        comitear, para participar de la transacción externa que también
        inserta la(s) asignación(es) inicial(es) (ver docstring del
        módulo). Si no se pasa, comportamiento standalone normal vía
        self._db.execute().
        """
        if self._existe_duplicado_reciente(
            activo_id, tipo, fecha, monto_total_minor, conn=conn,
        ):
            raise MovimientoDuplicadoError(
                f"Ya existe un movimiento idéntico (activo_id={activo_id}, "
                f"tipo={tipo}, fecha={fecha}, monto_total_minor={monto_total_minor}) "
                f"creado hace menos de {VENTANA_DUPLICADO_SEGUNDOS} segundos."
            )

        movimiento_id = nuevo_id()
        sql = """
            INSERT INTO movimientos_activo
                (id, activo_id, tipo, fecha, cantidad, precio_unitario_minor,
                 monto_total_minor, dolar_oficial_momento_minor, notas,
                 transaccion_id, comision_minor)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
        """
        params = (
            movimiento_id, activo_id, tipo, fecha, cantidad, precio_unitario_minor,
            monto_total_minor, dolar_oficial_momento_minor, notas,
            transaccion_id, comision_minor,
        )
        if conn is not None:
            conn.execute(sql, params)
        else:
            self._db.execute(sql, params)
        return movimiento_id

    # ----------------------------------------------------------
    # READ
    # ----------------------------------------------------------

    def obtener_por_id(self, movimiento_id: str) -> Optional[sqlite3.Row]:
        return (
            QueryBuilder("movimientos_activo", include_deleted=True)
            .where("id", movimiento_id)
            .ejecutar_uno(self._db.conn)
        )

    def listar_por_activo(self, activo_id: str) -> list[sqlite3.Row]:
        return (
            QueryBuilder("movimientos_activo", include_deleted=True)
            .where("activo_id", activo_id)
            .order("fecha")
            .ejecutar(self._db.conn)
        )

    def listar_por_tipo(self, activo_id: str, tipo: str) -> list[sqlite3.Row]:
        return (
            QueryBuilder("movimientos_activo", include_deleted=True)
            .where("activo_id", activo_id)
            .where("tipo", tipo)
            .order("fecha")
            .ejecutar(self._db.conn)
        )

    def transacciones_vinculadas(self) -> set[str]:
        """Ids de las transacciones del Registro que ya están vinculadas a algún movimiento."""
        filas = self._db.fetchall("SELECT DISTINCT transaccion_id FROM movimientos_activo WHERE transaccion_id IS NOT NULL;")
        return {fila["transaccion_id"] for fila in filas}

    # ----------------------------------------------------------
    # UPDATE
    # ----------------------------------------------------------

    def actualizar(self, movimiento_id: str, conn: Optional[sqlite3.Connection] = None, **kwargs: Any) -> bool:
        """
        Update parcial: solo las columnas pasadas (COLUMNAS_EDITABLES; None
        escribe NULL). Devuelve True si actualizó una fila. Si se pasa
        `conn`, participa de la transacción externa (sin commit).

        Raises:
            ValueError si alguna clave no es una columna editable.
        """
        desconocidas = set(kwargs) - set(COLUMNAS_EDITABLES)
        if desconocidas:
            raise ValueError(f"Columnas no editables en movimientos_activo: {', '.join(sorted(desconocidas))}.")
        if not kwargs:
            return False
        columnas = [c for c in COLUMNAS_EDITABLES if c in kwargs]
        sql = f"UPDATE movimientos_activo SET {', '.join(f'{c} = ?' for c in columnas)} WHERE id = ?;"
        params = (*(kwargs[c] for c in columnas), movimiento_id)
        if conn is not None:
            return conn.execute(sql, params).rowcount > 0
        cursor = self._db.conn.execute(sql, params)
        self._db.conn.commit()
        return cursor.rowcount > 0

    # ----------------------------------------------------------
    # DELETE
    # ----------------------------------------------------------

    def eliminar(self, movimiento_id: str, conn: Optional[sqlite3.Connection] = None) -> None:
        """
        DELETE físico del movimiento. Solo el movimiento en sí — NO borra
        sus asignaciones (eso es responsabilidad de quien orquesta, ver
        AsignacionesRepository.eliminar_por_movimiento(), que debe llamarse
        ANTES que este método dentro de la misma transacción para no violar
        la FK asignaciones.movimiento_id → movimientos_activo(id)) ni la
        transacción real vinculada (transaccion_id) — eso lo decide
        SavingsService.delete_movement() según el flag
        eliminar_transaccion_vinculada.

        Si se pasa `conn`, participa de la transacción externa.
        """
        sql = "DELETE FROM movimientos_activo WHERE id = ?;"
        if conn is not None:
            conn.execute(sql, (movimiento_id,))
            return
        self._db.execute(sql, (movimiento_id,))
