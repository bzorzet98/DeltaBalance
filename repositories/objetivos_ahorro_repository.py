"""
DeltaBalance — repositories/objetivos_ahorro_repository.py

Acceso a datos para la tabla `objetivos_ahorro`. Sin lógica de negocio: no
decide cuándo un objetivo pasa a 'cumplido'/'cancelado', no valida que
monto_meta_minor sea coherente con nada — eso vive en un futuro
SavingsService (Fase 2, bloque AHORROS, paso 2, todavía no existe).

Confirmado por grep en db/database.py (Fase 2, bloque AHORROS, paso 1): no
existe ningún método relacionado — no hay comportamiento previo que
replicar, el diseño sale directo del schema.

`objetivos_ahorro` NO tiene columna `deleted_at` ni `activa` — por eso
obtener_por_id()/listar() usan QueryBuilder con include_deleted=True
hardcodeado, mismo motivo que en los repositorios anteriores sin esa
columna. No hay un método de transición dedicado para `estado`
(activo/cumplido/cancelado) — a diferencia de otras tablas de esta fase
con transición de estado explícita, acá no se pidió una, así que `estado`
es simplemente uno más de los campos de actualizar() con NO_CAMBIAR.
"""

import sqlite3
from typing import Any, Optional

from db.database import DatabaseManager
from repositories._ids import nuevo_id
from db.query_builder import QueryBuilder
from repositories._sentinels import NO_CAMBIAR


class ObjetivosAhorroRepository:
    def __init__(self, db: DatabaseManager):
        self._db = db

    # ----------------------------------------------------------
    # CREATE
    # ----------------------------------------------------------

    def crear(
        self,
        nombre: str,
        monto_meta_minor: Optional[int] = None,
        fecha_meta: Optional[str] = None,
    ) -> str:
        """Inserta un objetivo de ahorro y devuelve su id (UUID). estado arranca en 'activo' (default de columna)."""
        objetivo_id = nuevo_id()
        self._db.execute(
            "INSERT INTO objetivos_ahorro (id, nombre, monto_meta_minor, fecha_meta) VALUES (?, ?, ?, ?);",
            (objetivo_id, nombre, monto_meta_minor, fecha_meta),
        )
        return objetivo_id

    # ----------------------------------------------------------
    # READ
    # ----------------------------------------------------------

    def obtener_por_id(self, objetivo_id: str) -> Optional[sqlite3.Row]:
        return (
            QueryBuilder("objetivos_ahorro", include_deleted=True)
            .where("id", objetivo_id)
            .ejecutar_uno(self._db.conn)
        )

    def listar(self, estado: Optional[str] = None) -> list[sqlite3.Row]:
        return (
            QueryBuilder("objetivos_ahorro", include_deleted=True)
            .where("estado", estado)
            .order("nombre")
            .ejecutar(self._db.conn)
        )

    # ----------------------------------------------------------
    # UPDATE
    # ----------------------------------------------------------

    def actualizar(
        self,
        objetivo_id: str,
        nombre: Any = NO_CAMBIAR,
        monto_meta_minor: Any = NO_CAMBIAR,
        fecha_meta: Any = NO_CAMBIAR,
        estado: Any = NO_CAMBIAR,
        conn: Optional[sqlite3.Connection] = None,
    ) -> bool:
        """
        Update parcial. Default NO_CAMBIAR = no tocar ese campo. Pasar
        None explícito escribe NULL a propósito (ej. fecha_meta=None para
        quitar una fecha meta, monto_meta_minor=None si el objetivo pasa a
        ser sin monto fijo) — igual que TransaccionesRepository.actualizar().
        """
        campos, valores = [], []
        if nombre           is not NO_CAMBIAR: campos.append("nombre = ?");           valores.append(nombre)
        if monto_meta_minor is not NO_CAMBIAR: campos.append("monto_meta_minor = ?"); valores.append(monto_meta_minor)
        if fecha_meta        is not NO_CAMBIAR: campos.append("fecha_meta = ?");        valores.append(fecha_meta)
        if estado            is not NO_CAMBIAR: campos.append("estado = ?");            valores.append(estado)
        if not campos:
            return False
        valores.append(objetivo_id)
        sql = f"UPDATE objetivos_ahorro SET {', '.join(campos)} WHERE id = ?;"
        if conn is not None:
            conn.execute(sql, tuple(valores))
        else:
            self._db.execute(sql, tuple(valores))
        return True

    # ----------------------------------------------------------
    # DELETE
    # ----------------------------------------------------------

    def eliminar(self, objetivo_id: str, conn: Optional[sqlite3.Connection] = None) -> None:
        """
        DELETE físico (la tabla no tiene deleted_at ni activa). Las FK de
        asignaciones / activo_objetivos lo rechazan si quedan filas que lo
        referencian: quien lo llama las mueve o las borra antes, en la misma
        transacción (`conn`).
        """
        sql = "DELETE FROM objetivos_ahorro WHERE id = ?;"
        if conn is not None:
            conn.execute(sql, (objetivo_id,))
        else:
            self._db.execute(sql, (objetivo_id,))
