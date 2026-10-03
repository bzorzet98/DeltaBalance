"""
DeltaBalance — repositories/ingresos_proyectados_repository.py

Acceso a datos para la tabla `ingresos_proyectados` en su estructura final
(db/schema_migrations.py, reestructurar_presupuestos_ingresos()): una fila
por ingreso esperado del mes, con su monto estimado y el real cobrado
(monto_real_minor), y si se repite todos los meses (es_recurrente). Ya no
hay `estado` ni monto_percibido_minor.

Sin lógica de negocio: no valida montos ni normaliza el concepto, no
decide qué se puede editar — eso vive en services/ingresos_service.py.
Recibe moneda_id y montos ya resueltos en minor units.

Las lecturas traen la fila enriquecida con la moneda (currency_code,
currency_symbol, decimales — JOIN a monedas), igual que
DeudasRepository, para que la pantalla muestre los montos sin otra
consulta. La tabla no tiene `deleted_at`: SQL explícito, sin QueryBuilder.

copiar_recurrentes() no duplica: una fila recurrente del mes origen se
saltea si el mes destino ya tiene una con el mismo concepto (el service
guarda el concepto normalizado, así que la comparación es exacta).
"""

import sqlite3
from typing import Any, Optional

from db.database import DatabaseManager
from repositories._ids import nuevo_id

# actualizar(): columnas que se pueden escribir (las claves de **kwargs van
# al SQL, así que nunca se acepta una que no esté acá).
COLUMNAS_EDITABLES = (
    "concepto", "monto_estimado_minor", "monto_real_minor", "moneda_id", "mes", "anio", "es_recurrente", "notas",
)

# Lectura enriquecida: columnas propias + la moneda.
_SELECT = """
    SELECT i.*, m.codigo AS currency_code, m.simbolo AS currency_symbol, m.decimales
    FROM ingresos_proyectados i
    JOIN monedas m ON m.id = i.moneda_id
"""
# Orden de alta (rowid): con ids UUID, ordenar por id sería al azar.
_ORDEN = " ORDER BY i.rowid;"


class IngresosProyectadosRepository:
    def __init__(self, db: DatabaseManager):
        self._db = db

    # ----------------------------------------------------------
    # CREATE
    # ----------------------------------------------------------

    def crear(
        self,
        concepto: str,
        monto_estimado_minor: int,
        monto_real_minor: int,
        moneda_id: int,
        mes: int,
        anio: int,
        es_recurrente: int = 0,
        notas: Optional[str] = None,
    ) -> str:
        """Inserta un ingreso y devuelve su id (UUID, repositories/_ids.py)."""
        ingreso_id = nuevo_id()
        self._db.conn.execute(
            """
            INSERT INTO ingresos_proyectados
                (id, concepto, monto_estimado_minor, monto_real_minor, moneda_id, mes, anio, es_recurrente, notas)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
            """,
            (ingreso_id, concepto, monto_estimado_minor, monto_real_minor, moneda_id, mes, anio, es_recurrente, notas),
        )
        self._db.conn.commit()
        return ingreso_id

    # ----------------------------------------------------------
    # READ
    # ----------------------------------------------------------

    def obtener_por_id(self, ingreso_id: str) -> Optional[sqlite3.Row]:
        return self._db.fetchone(_SELECT + " WHERE i.id = ?;", (ingreso_id,))

    def listar_por_mes(self, mes: int, anio: int) -> list[sqlite3.Row]:
        """Los ingresos de ese mes/año, en orden de alta."""
        return self._db.fetchall(_SELECT + " WHERE i.mes = ? AND i.anio = ?" + _ORDEN, (mes, anio))

    # ----------------------------------------------------------
    # UPDATE
    # ----------------------------------------------------------

    def actualizar(self, ingreso_id: str, **kwargs: Any) -> bool:
        """
        Update parcial: solo las columnas pasadas (COLUMNAS_EDITABLES; None
        escribe NULL). Devuelve True si actualizó una fila.

        Raises:
            ValueError si alguna clave no es una columna editable.
        """
        desconocidas = set(kwargs) - set(COLUMNAS_EDITABLES)
        if desconocidas:
            raise ValueError(f"Columnas no editables en ingresos_proyectados: {', '.join(sorted(desconocidas))}.")
        if not kwargs:
            return False
        columnas = [c for c in COLUMNAS_EDITABLES if c in kwargs]
        cursor = self._db.conn.execute(
            f"UPDATE ingresos_proyectados SET {', '.join(f'{c} = ?' for c in columnas)} WHERE id = ?;",
            (*(kwargs[c] for c in columnas), ingreso_id),
        )
        self._db.conn.commit()
        return cursor.rowcount > 0

    # ----------------------------------------------------------
    # DELETE
    # ----------------------------------------------------------

    def eliminar(self, ingreso_id: str) -> bool:
        """DELETE físico. Devuelve True si borró una fila."""
        cursor = self._db.conn.execute("DELETE FROM ingresos_proyectados WHERE id = ?;", (ingreso_id,))
        self._db.conn.commit()
        return cursor.rowcount > 0

    # ----------------------------------------------------------
    # COPIAR RECURRENTES
    # ----------------------------------------------------------

    def copiar_recurrentes(self, mes_origen: int, anio_origen: int, mes_destino: int, anio_destino: int) -> int:
        """
        Copia al mes destino cada fila con es_recurrente=1 del mes origen,
        con monto_real_minor=0 (el estimado, la moneda y las notas se
        mantienen; sigue siendo recurrente). Saltea las que el destino ya
        tiene con el mismo concepto. Una sola transacción. Devuelve
        cuántas copió.
        """
        origen = self._db.fetchall(
            """
            SELECT o.* FROM ingresos_proyectados o
            WHERE o.mes = ? AND o.anio = ? AND o.es_recurrente = 1
              AND NOT EXISTS (
                  SELECT 1 FROM ingresos_proyectados d
                  WHERE d.mes = ? AND d.anio = ? AND d.concepto = o.concepto
              )
            ORDER BY o.rowid;
            """,
            (mes_origen, anio_origen, mes_destino, anio_destino),
        )
        with self._db.transaction() as conn:
            for fila in origen:
                conn.execute(
                    """
                    INSERT INTO ingresos_proyectados
                        (id, concepto, monto_estimado_minor, monto_real_minor, moneda_id, mes, anio, es_recurrente, notas)
                    VALUES (?, ?, ?, 0, ?, ?, ?, 1, ?);
                    """,
                    (
                        nuevo_id(), fila["concepto"], fila["monto_estimado_minor"], fila["moneda_id"],
                        mes_destino, anio_destino, fila["notas"],
                    ),
                )
        return len(origen)
