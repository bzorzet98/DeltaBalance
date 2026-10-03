"""
DeltaBalance — repositories/presupuestos_repository.py

Acceso a datos para la tabla `presupuestos` en su estructura final
(db/schema_migrations.py, reestructurar_presupuestos_ingresos()), con dos
tipos de fila:
    'fijo'      concepto libre (ALQUILER, SEGURO…), estimado y real cargados
                a mano (monto_real_minor).
    'variable'  una categoría del Registro (categoria_id) o el ítem especial
                COMPARTIDOS (categoria_id NULL + concepto) — su real NO se
                guarda: lo calcula services/presupuestos_service.py.

Sin lógica de negocio: no valida el tipo ni la categoría, no decide qué
columnas usa cada tipo, no calcula ningún real — eso vive en
services/presupuestos_service.py. Recibe moneda_id y montos ya resueltos en
minor units.

Las lecturas traen la fila enriquecida con la moneda (currency_code,
currency_symbol, decimales — JOIN a monedas) y, para las variables, el
nombre de su categoría (subcategoria, categoria_principal — LEFT JOIN a
categorias: fijos y COMPARTIDOS no tienen). La tabla no tiene
`deleted_at`: SQL explícito, sin QueryBuilder.

copiar_recurrentes() no duplica: una fila recurrente del mes origen se
saltea si el mes destino ya tiene una del mismo tipo con la misma
categoría y el mismo concepto (comparación con IS: NULL = NULL), o sea el
mismo fijo, la misma categoría o el mismo ítem COMPARTIDOS.
"""

import sqlite3
from typing import Any, Optional

from db.database import DatabaseManager
from repositories._ids import nuevo_id

# actualizar(): columnas que se pueden escribir (las claves de **kwargs van
# al SQL, así que nunca se acepta una que no esté acá).
COLUMNAS_EDITABLES = (
    "tipo", "concepto", "categoria_id", "monto_estimado_minor", "monto_real_minor",
    "moneda_id", "mes", "anio", "es_recurrente",
)

# Lectura enriquecida: columnas propias + la moneda + la categoría (si tiene).
_SELECT = """
    SELECT p.*, m.codigo AS currency_code, m.simbolo AS currency_symbol, m.decimales,
           cat.subcategoria, cat.categoria_principal
    FROM presupuestos p
    JOIN monedas m ON m.id = p.moneda_id
    LEFT JOIN categorias cat ON cat.id = p.categoria_id
"""
# Fijos primero ('fijo' < 'variable'), después en orden de alta (rowid: con
# ids UUID, ordenar por id sería al azar).
_ORDEN = " ORDER BY p.tipo, p.rowid;"


class PresupuestosRepository:
    def __init__(self, db: DatabaseManager):
        self._db = db

    # ----------------------------------------------------------
    # CREATE
    # ----------------------------------------------------------

    def crear(
        self,
        tipo: str,
        moneda_id: int,
        mes: int,
        anio: int,
        monto_estimado_minor: int,
        concepto: Optional[str] = None,
        categoria_id: Optional[str] = None,
        es_recurrente: int = 0,
        monto_real_minor: int = 0,
    ) -> str:
        """Inserta un presupuesto y devuelve su id (UUID, repositories/_ids.py)."""
        presupuesto_id = nuevo_id()
        self._db.conn.execute(
            """
            INSERT INTO presupuestos
                (id, tipo, concepto, categoria_id, monto_estimado_minor, monto_real_minor,
                 moneda_id, mes, anio, es_recurrente)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """,
            (
                presupuesto_id, tipo, concepto, categoria_id, monto_estimado_minor, monto_real_minor,
                moneda_id, mes, anio, es_recurrente,
            ),
        )
        self._db.conn.commit()
        return presupuesto_id

    # ----------------------------------------------------------
    # READ
    # ----------------------------------------------------------

    def obtener_por_id(self, presupuesto_id: str) -> Optional[sqlite3.Row]:
        return self._db.fetchone(_SELECT + " WHERE p.id = ?;", (presupuesto_id,))

    def listar_por_mes(self, mes: int, anio: int) -> list[sqlite3.Row]:
        """
        Los presupuestos de ese mes/año, separados por tipo: primero todos
        los 'fijo' y después todos los 'variable', cada grupo en orden de
        alta.
        """
        return self._db.fetchall(_SELECT + " WHERE p.mes = ? AND p.anio = ?" + _ORDEN, (mes, anio))

    # ----------------------------------------------------------
    # UPDATE
    # ----------------------------------------------------------

    def actualizar(self, presupuesto_id: str, **kwargs: Any) -> bool:
        """
        Update parcial: solo las columnas pasadas (COLUMNAS_EDITABLES; None
        escribe NULL). Devuelve True si actualizó una fila.

        Raises:
            ValueError si alguna clave no es una columna editable.
        """
        desconocidas = set(kwargs) - set(COLUMNAS_EDITABLES)
        if desconocidas:
            raise ValueError(f"Columnas no editables en presupuestos: {', '.join(sorted(desconocidas))}.")
        if not kwargs:
            return False
        columnas = [c for c in COLUMNAS_EDITABLES if c in kwargs]
        cursor = self._db.conn.execute(
            f"UPDATE presupuestos SET {', '.join(f'{c} = ?' for c in columnas)} WHERE id = ?;",
            (*(kwargs[c] for c in columnas), presupuesto_id),
        )
        self._db.conn.commit()
        return cursor.rowcount > 0

    # ----------------------------------------------------------
    # DELETE
    # ----------------------------------------------------------

    def eliminar(self, presupuesto_id: str) -> bool:
        """DELETE físico. Devuelve True si borró una fila."""
        cursor = self._db.conn.execute("DELETE FROM presupuestos WHERE id = ?;", (presupuesto_id,))
        self._db.conn.commit()
        return cursor.rowcount > 0

    # ----------------------------------------------------------
    # COPIAR RECURRENTES
    # ----------------------------------------------------------

    def copiar_recurrentes(self, mes_origen: int, anio_origen: int, mes_destino: int, anio_destino: int) -> int:
        """
        Copia al mes destino cada fila con es_recurrente=1 del mes origen
        (de los dos tipos), con monto_real_minor=0: el estimado, la moneda,
        el concepto y la categoría se mantienen; sigue siendo recurrente.
        Saltea las que el destino ya tiene (ver docstring del módulo). Una
        sola transacción. Devuelve cuántas copió.
        """
        origen = self._db.fetchall(
            """
            SELECT o.* FROM presupuestos o
            WHERE o.mes = ? AND o.anio = ? AND o.es_recurrente = 1
              AND NOT EXISTS (
                  SELECT 1 FROM presupuestos d
                  WHERE d.mes = ? AND d.anio = ? AND d.tipo = o.tipo
                    AND d.categoria_id IS o.categoria_id AND d.concepto IS o.concepto
              )
            ORDER BY o.tipo, o.rowid;
            """,
            (mes_origen, anio_origen, mes_destino, anio_destino),
        )
        with self._db.transaction() as conn:
            for fila in origen:
                conn.execute(
                    """
                    INSERT INTO presupuestos
                        (id, tipo, concepto, categoria_id, monto_estimado_minor, monto_real_minor,
                         moneda_id, mes, anio, es_recurrente)
                    VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?, 1);
                    """,
                    (
                        nuevo_id(), fila["tipo"], fila["concepto"], fila["categoria_id"], fila["monto_estimado_minor"],
                        fila["moneda_id"], mes_destino, anio_destino,
                    ),
                )
        return len(origen)
