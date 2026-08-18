"""
DeltaBalance — repositories/activos_financieros_repository.py

Acceso a datos para la tabla `activos_financieros`. Sin lógica de negocio:
no decide qué activos "deberían" existir, no calcula ninguna cotización ni
rendimiento — eso vive en un futuro SavingsService (Fase 2, bloque
AHORROS, paso 2, todavía no existe).

Confirmado por grep en db/database.py (Fase 2, bloque AHORROS, paso 1):
NO existe ningún método relacionado con activos_financieros/
movimientos_activo/objetivos_ahorro/asignaciones en el código legacy — las
cuatro tablas se agregaron directo al schema en una fase de diseño previa
(ver docs/DATA_MODEL_DECISIONS.md sección 4) sin pasar nunca por
database.py. Por eso no hay comentario de deprecación que agregar ahí: no
hay nada que deprecar.

`activos_financieros` NO tiene columna `deleted_at` — por eso
obtener_por_id()/listar() usan QueryBuilder con include_deleted=True
hardcodeado, mismo motivo que en los repositorios anteriores sin esa
columna. SÍ tiene `activa` (como `cuentas`/`categorias`), así que el
soft-delete sigue el mismo patrón: desactivar()/activar() son métodos
dedicados, separados de actualizar() — actualizar() NUNCA toca `activa`.
"""

import sqlite3
from typing import Any, Optional

from db.database import DatabaseManager
from db.query_builder import QueryBuilder
from repositories._sentinels import NO_CAMBIAR


class ActivosFinancierosRepository:
    def __init__(self, db: DatabaseManager):
        self._db = db

    # ----------------------------------------------------------
    # CREATE
    # ----------------------------------------------------------

    def crear(self, nombre: str, tipo: str, moneda_id: int) -> int:
        """Inserta un activo financiero. activa arranca en 1 (default de columna)."""
        return self._db.execute(
            "INSERT INTO activos_financieros (nombre, tipo, moneda_id) VALUES (?, ?, ?);",
            (nombre, tipo, moneda_id),
        )

    # ----------------------------------------------------------
    # READ
    # ----------------------------------------------------------

    def obtener_por_id(self, activo_id: int) -> Optional[sqlite3.Row]:
        return (
            QueryBuilder("activos_financieros", include_deleted=True)
            .where("id", activo_id)
            .ejecutar_uno(self._db.conn)
        )

    def listar(self, tipo: Optional[str] = None, solo_activos: bool = True) -> list[sqlite3.Row]:
        """Filtros AND-combinados. solo_activos=True filtra activa=1 (default)."""
        builder = QueryBuilder("activos_financieros", include_deleted=True).where("tipo", tipo)
        if solo_activos:
            builder = builder.where("activa", 1)
        return builder.order("nombre").ejecutar(self._db.conn)

    # ----------------------------------------------------------
    # UPDATE
    # ----------------------------------------------------------

    def actualizar(
        self,
        activo_id: int,
        nombre: Any = NO_CAMBIAR,
        tipo: Any = NO_CAMBIAR,
        moneda_id: Any = NO_CAMBIAR,
        conn: Optional[sqlite3.Connection] = None,
    ) -> bool:
        """
        Update parcial de nombre/tipo/moneda_id. Default NO_CAMBIAR = no
        tocar ese campo. NO incluye `activa` a propósito — eso es
        transición exclusiva de desactivar()/activar(), mismo patrón que
        CategoriasRepository.
        """
        campos, valores = [], []
        if nombre    is not NO_CAMBIAR: campos.append("nombre = ?");    valores.append(nombre)
        if tipo      is not NO_CAMBIAR: campos.append("tipo = ?");      valores.append(tipo)
        if moneda_id is not NO_CAMBIAR: campos.append("moneda_id = ?"); valores.append(moneda_id)
        if not campos:
            return False
        valores.append(activo_id)
        sql = f"UPDATE activos_financieros SET {', '.join(campos)} WHERE id = ?;"
        if conn is not None:
            conn.execute(sql, tuple(valores))
        else:
            self._db.execute(sql, tuple(valores))
        return True

    # ----------------------------------------------------------
    # SOFT-DELETE
    # ----------------------------------------------------------

    def desactivar(self, activo_id: int) -> None:
        """Soft-delete: activa = 0. No valida si el activo tiene movimientos asociados."""
        self._db.execute("UPDATE activos_financieros SET activa = 0 WHERE id = ?;", (activo_id,))

    def activar(self, activo_id: int) -> None:
        self._db.execute("UPDATE activos_financieros SET activa = 1 WHERE id = ?;", (activo_id,))
