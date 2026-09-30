"""
DeltaBalance — repositories/deudas_repository.py

Acceso a datos para la tabla `deudas` en su estructura final (db/schema.sql,
docs/DATA_MODEL_DECISIONS.md sección 22): un LIBRO DE MOVIMIENTOS separado
en dos tabs — 'me_deben' (lo que te deben) y 'debo' (lo que debés) — donde
cada fila tiene monto_minor CON SIGNO (positivo = entrada, la deuda crece;
negativo = salida, un pago que la baja). El saldo de una persona en un tab
es SUM(monto_minor). No hay pendiente, estado ni tabla de pagos.

Sin lógica de negocio: no valida el tab, no normaliza el nombre de la
persona, no decide nada sobre el signo — eso vive en
services/debts_service.py. Recibe moneda_id y montos ya resueltos en minor
units.

Las lecturas traen la fila enriquecida con la moneda (currency_code,
currency_symbol, decimales — JOIN a monedas), para que las pantallas
muestren el monto sin otra consulta. `deudas` no tiene `deleted_at`:
QueryBuilder no se usa acá para no inyectar ese filtro; las consultas son
SQL explícito.

actualizar() también acepta entidad_persona y moneda_id (no estaban en el
pedido): la pantalla edita todas las celdas, y corregir un nombre mal
tipeado es la corrección más común (CLAUDE.md §4). listar_por_origen() lo
usa FeesService.delete_purchase() para no borrar una compra de la que
depende una deuda.

Chequeo de duplicados (DeudaDuplicadaError): mismo mecanismo que
TransaccionesRepository.crear() — seguridad contra doble-click/doble-Enter,
no una regla de negocio.
"""

import calendar
import sqlite3
from typing import Any, Optional

from db.database import DatabaseManager
from repositories._sentinels import NO_CAMBIAR

# Valores del CHECK de deudas.tab (db/schema.sql).
TABS = ("me_deben", "debo")

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
    crear() encontró una fila con la misma entidad_persona/tab/monto_minor/
    fecha creada hace menos de VENTANA_DUPLICADO_SEGUNDOS (doble-click).
    """


class DeudasRepository:
    def __init__(self, db: DatabaseManager):
        self._db = db

    # ----------------------------------------------------------
    # CREATE
    # ----------------------------------------------------------

    def _existe_duplicado_reciente(self, entidad_persona: str, tab: str, monto_minor: int, fecha: str) -> bool:
        sql = """
            SELECT 1 FROM deudas
            WHERE entidad_persona = ? AND tab = ? AND monto_minor = ? AND fecha = ?
              AND (strftime('%s', 'now') - strftime('%s', creada_en)) < ?
            LIMIT 1;
        """
        params = (entidad_persona, tab, monto_minor, fecha, VENTANA_DUPLICADO_SEGUNDOS)
        return self._db.conn.execute(sql, params).fetchone() is not None

    def crear(
        self,
        entidad_persona: str,
        concepto: Optional[str],
        tab: str,
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
        if self._existe_duplicado_reciente(entidad_persona, tab, monto_minor, fecha):
            raise DeudaDuplicadaError(
                f"Ya existe un movimiento idéntico (entidad_persona='{entidad_persona}', tab='{tab}', "
                f"monto_minor={monto_minor}, fecha={fecha}) creado hace menos de "
                f"{VENTANA_DUPLICADO_SEGUNDOS} segundos."
            )
        sql = """
            INSERT INTO deudas
                (entidad_persona, concepto, tab, monto_minor, moneda_id, fecha, notas, origen_tipo, origen_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
        """
        params = (entidad_persona, concepto, tab, monto_minor, moneda_id, fecha, notas, origen_tipo, origen_id)
        if conn is not None:
            return conn.execute(sql, params).lastrowid
        return self._db.execute(sql, params)

    # ----------------------------------------------------------
    # READ
    # ----------------------------------------------------------

    def obtener_por_id(self, deuda_id: int) -> Optional[sqlite3.Row]:
        return self._db.fetchone(_SELECT + " WHERE d.id = ?;", (deuda_id,))

    def listar_por_tab(self, tab: str, mes: Optional[int] = None, anio: Optional[int] = None) -> list[sqlite3.Row]:
        """
        Filas de un tab, la más nueva primero. Con mes y anio: solo las de
        ese mes; con anio solo: las de ese año; sin ninguno: todas.
        """
        sql = _SELECT + " WHERE d.tab = ?"
        params: list[Any] = [tab]
        if anio is not None:
            if mes is not None:
                desde = f"{anio:04d}-{mes:02d}-01"
                hasta = f"{anio:04d}-{mes:02d}-{calendar.monthrange(anio, mes)[1]:02d}"
            else:
                desde, hasta = f"{anio:04d}-01-01", f"{anio:04d}-12-31"
            sql += " AND d.fecha >= ? AND d.fecha <= ?"
            params += [desde, hasta]
        elif mes is not None:
            raise ValueError("listar_por_tab(): mes sin anio.")
        return self._db.fetchall(sql + _ORDEN, tuple(params))

    def listar_por_persona(self, entidad_persona: str, tab: Optional[str] = None) -> list[sqlite3.Row]:
        """Filas de una persona (nombre exacto), opcionalmente de un tab; la más nueva primero."""
        sql = _SELECT + " WHERE d.entidad_persona = ?"
        params: list[Any] = [entidad_persona]
        if tab is not None:
            sql += " AND d.tab = ?"
            params.append(tab)
        return self._db.fetchall(sql + _ORDEN, tuple(params))

    def listar_por_origen(self, origen_tipo: str, origen_id: int) -> list[sqlite3.Row]:
        """Filas generadas desde un registro puntual (ej. 'compra_cuotas', <compra>) — ver docstring del módulo."""
        return self._db.fetchall(
            _SELECT + " WHERE d.origen_tipo = ? AND d.origen_id = ?" + _ORDEN, (origen_tipo, origen_id),
        )

    # ----------------------------------------------------------
    # SALDOS
    # ----------------------------------------------------------

    def get_saldo_neto_por_persona(self, tab: str, hasta_fecha: str) -> list[sqlite3.Row]:
        """(entidad_persona, moneda_id, saldo) de un tab: SUM(monto_minor) de las filas con fecha <= hasta_fecha."""
        return self._db.fetchall(
            """
            SELECT entidad_persona, moneda_id, SUM(monto_minor) AS saldo
            FROM deudas
            WHERE tab = ? AND fecha <= ?
            GROUP BY entidad_persona, moneda_id
            ORDER BY entidad_persona;
            """,
            (tab, hasta_fecha),
        )

    # ----------------------------------------------------------
    # UPDATE
    # ----------------------------------------------------------

    def actualizar(
        self,
        deuda_id: int,
        concepto: Any = NO_CAMBIAR,
        tab: Any = NO_CAMBIAR,
        monto_minor: Any = NO_CAMBIAR,
        fecha: Any = NO_CAMBIAR,
        notas: Any = NO_CAMBIAR,
        entidad_persona: Any = NO_CAMBIAR,
        moneda_id: Any = NO_CAMBIAR,
    ) -> bool:
        """
        Update parcial: NO_CAMBIAR = no tocar ese campo; None explícito
        escribe NULL (concepto/notas). Devuelve True si actualizó una fila.
        """
        campos, valores = [], []
        for columna, valor in (
            ("entidad_persona", entidad_persona), ("concepto", concepto), ("tab", tab),
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
