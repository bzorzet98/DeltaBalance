"""
DeltaBalance — repositories/deudas_repository.py

Acceso a datos para la tabla `deudas`, reestructurada como LIBRO DE
MOVIMIENTOS (db/schema_migrations.py reestructurar_deudas(),
docs/DATA_MODEL_DECISIONS.md sección 22): cada fila es un monto con
dirección — tipo 'a_favor' (te deben más) o 'en_contra' (debés más, o te
pagaron) — y el saldo con una persona es la suma con signo de sus filas.
No hay pendiente, ni estado, ni deuda_pagos: un pago es una fila más, de
tipo opuesto. La tabla vieja quedó como `deudas_old`.

Sin lógica de negocio: no valida el tipo, no normaliza el nombre de la
persona, no decide el signo de un monto — eso vive en
services/debts_service.py. Recibe moneda_id y montos ya resueltos en minor
units (siempre positivos: la dirección es `tipo`).

Las lecturas traen la fila enriquecida con la moneda (currency_code,
currency_symbol, decimales — JOIN a monedas), para que las pantallas
muestren el monto sin otra consulta. `deudas` no tiene `deleted_at`:
QueryBuilder no se usa acá para no inyectar ese filtro; las consultas son
SQL explícito.

listar_por_origen() se conserva (no estaba en el pedido de la
reestructuración): FeesService.delete_purchase() lo usa para no borrar una
compra de la que depende una deuda.

Chequeo de duplicados (DeudaDuplicadaError): mismo mecanismo que
TransaccionesRepository.crear() — seguridad contra doble-click/doble-Enter,
no una regla de negocio.
"""

import sqlite3
from typing import Any, Optional

from db.database import DatabaseManager
from repositories._sentinels import NO_CAMBIAR

VENTANA_DUPLICADO_SEGUNDOS = 5

# Lectura enriquecida: columnas propias + la moneda.
_SELECT = """
    SELECT d.*, m.codigo AS currency_code, m.simbolo AS currency_symbol, m.decimales
    FROM deudas d
    JOIN monedas m ON m.id = d.moneda_id
"""
_ORDEN = " ORDER BY d.fecha DESC, d.id DESC;"


class DeudaDuplicadaError(Exception):
    """
    crear() encontró una fila con la misma entidad_persona/tipo/monto_minor/
    fecha creada hace menos de VENTANA_DUPLICADO_SEGUNDOS (doble-click).
    """


class DeudasRepository:
    def __init__(self, db: DatabaseManager):
        self._db = db

    # ----------------------------------------------------------
    # CREATE
    # ----------------------------------------------------------

    def _existe_duplicado_reciente(self, entidad_persona: str, tipo: str, monto_minor: int, fecha: str) -> bool:
        sql = """
            SELECT 1 FROM deudas
            WHERE entidad_persona = ? AND tipo = ? AND monto_minor = ? AND fecha = ?
              AND (strftime('%s', 'now') - strftime('%s', creada_en)) < ?
            LIMIT 1;
        """
        params = (entidad_persona, tipo, monto_minor, fecha, VENTANA_DUPLICADO_SEGUNDOS)
        return self._db.conn.execute(sql, params).fetchone() is not None

    def crear(
        self,
        entidad_persona: str,
        concepto: Optional[str],
        tipo: str,
        monto_minor: int,
        moneda_id: int,
        fecha: str,
        notas: Optional[str] = None,
        origen_tipo: str = "manual",
        origen_id: Optional[int] = None,
        conn: Optional[sqlite3.Connection] = None,
    ) -> int:
        """
        Inserta una fila y devuelve su id. DeudaDuplicadaError si es idéntica
        a una creada hace menos de VENTANA_DUPLICADO_SEGUNDOS.
        """
        if self._existe_duplicado_reciente(entidad_persona, tipo, monto_minor, fecha):
            raise DeudaDuplicadaError(
                f"Ya existe una deuda idéntica (entidad_persona='{entidad_persona}', tipo='{tipo}', "
                f"monto_minor={monto_minor}, fecha={fecha}) creada hace menos de "
                f"{VENTANA_DUPLICADO_SEGUNDOS} segundos."
            )
        sql = """
            INSERT INTO deudas
                (entidad_persona, concepto, tipo, monto_minor, moneda_id, fecha, notas, origen_tipo, origen_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
        """
        params = (entidad_persona, concepto, tipo, monto_minor, moneda_id, fecha, notas, origen_tipo, origen_id)
        if conn is not None:
            return conn.execute(sql, params).lastrowid
        return self._db.execute(sql, params)

    # ----------------------------------------------------------
    # READ
    # ----------------------------------------------------------

    def obtener_por_id(self, deuda_id: int) -> Optional[sqlite3.Row]:
        return self._db.fetchone(_SELECT + " WHERE d.id = ?;", (deuda_id,))

    def listar_por_persona(self, entidad_persona: str, moneda_id: Optional[int] = None) -> list[sqlite3.Row]:
        """Filas de una persona (nombre exacto), opcionalmente de una moneda; la más nueva primero."""
        sql = _SELECT + " WHERE d.entidad_persona = ?"
        params: list[Any] = [entidad_persona]
        if moneda_id is not None:
            sql += " AND d.moneda_id = ?"
            params.append(moneda_id)
        return self._db.fetchall(sql + _ORDEN, tuple(params))

    def listar_por_periodo(self, fecha_desde: str, fecha_hasta: str) -> list[sqlite3.Row]:
        """Filas con fecha_desde <= fecha <= fecha_hasta ('YYYY-MM-DD'); la más nueva primero."""
        return self._db.fetchall(_SELECT + " WHERE d.fecha >= ? AND d.fecha <= ?" + _ORDEN, (fecha_desde, fecha_hasta))

    def listar_todo(self) -> list[sqlite3.Row]:
        return self._db.fetchall(_SELECT + _ORDEN)

    def listar_por_origen(self, origen_tipo: str, origen_id: int) -> list[sqlite3.Row]:
        """Filas generadas desde un registro puntual (ej. 'compra_cuotas', <compra>) — ver docstring del módulo."""
        return self._db.fetchall(
            _SELECT + " WHERE d.origen_tipo = ? AND d.origen_id = ?" + _ORDEN, (origen_tipo, origen_id),
        )

    # ----------------------------------------------------------
    # SALDOS (suma con signo: a_favor suma, en_contra resta)
    # ----------------------------------------------------------

    def get_saldo_neto_por_persona(self, hasta_fecha: str) -> list[sqlite3.Row]:
        """(entidad_persona, moneda_id, saldo) de todas las filas con fecha <= hasta_fecha."""
        return self._db.fetchall(
            """
            SELECT entidad_persona, moneda_id,
                   SUM(CASE WHEN tipo = 'a_favor' THEN monto_minor ELSE -monto_minor END) AS saldo
            FROM deudas
            WHERE fecha <= ?
            GROUP BY entidad_persona, moneda_id
            ORDER BY entidad_persona;
            """,
            (hasta_fecha,),
        )

    def get_saldo_persona(self, entidad_persona: str, moneda_id: int, hasta_fecha: str) -> int:
        """Saldo con signo de una persona (nombre exacto) en una moneda, con fecha <= hasta_fecha (0 si no hay filas)."""
        fila = self._db.fetchone(
            """
            SELECT COALESCE(SUM(CASE WHEN tipo = 'a_favor' THEN monto_minor ELSE -monto_minor END), 0) AS saldo
            FROM deudas
            WHERE entidad_persona = ? AND moneda_id = ? AND fecha <= ?;
            """,
            (entidad_persona, moneda_id, hasta_fecha),
        )
        return fila["saldo"]

    # ----------------------------------------------------------
    # UPDATE
    # ----------------------------------------------------------

    def actualizar(
        self,
        deuda_id: int,
        entidad_persona: Any = NO_CAMBIAR,
        concepto: Any = NO_CAMBIAR,
        tipo: Any = NO_CAMBIAR,
        monto_minor: Any = NO_CAMBIAR,
        moneda_id: Any = NO_CAMBIAR,
        fecha: Any = NO_CAMBIAR,
        notas: Any = NO_CAMBIAR,
    ) -> bool:
        """
        Update parcial: NO_CAMBIAR = no tocar ese campo; None explícito
        escribe NULL (concepto/notas). entidad_persona y moneda_id no estaban
        en el pedido original, pero la pantalla edita todas las celdas.
        Devuelve True si actualizó una fila.
        """
        campos, valores = [], []
        for columna, valor in (
            ("entidad_persona", entidad_persona), ("concepto", concepto), ("tipo", tipo),
            ("monto_minor", monto_minor), ("moneda_id", moneda_id), ("fecha", fecha), ("notas", notas),
        ):
            if valor is not NO_CAMBIAR:
                campos.append(f"{columna} = ?")
                valores.append(valor)
        if not campos:
            return False
        cursor = self._db.conn.execute(
            f"UPDATE deudas SET {', '.join(campos)} WHERE id = ?;", (*valores, deuda_id),
        )
        self._db.conn.commit()
        return cursor.rowcount > 0

    # ----------------------------------------------------------
    # DELETE
    # ----------------------------------------------------------

    def eliminar(self, deuda_id: int) -> bool:
        """DELETE físico. Devuelve True si borró una fila."""
        cursor = self._db.conn.execute("DELETE FROM deudas WHERE id = ?;", (deuda_id,))
        self._db.conn.commit()
        return cursor.rowcount > 0
