"""
DeltaBalance — repositories/activo_objetivos_repository.py

Acceso a datos para la tabla `activo_objetivos` (db/schema.sql): qué
porcentaje de un activo financiero es de cada objetivo de ahorro — el
reparto VIGENTE del activo. A partir de él SavingsService generaba las
`asignaciones` de cada movimiento nuevo.

DEPRECATED (docs/DATA_MODEL_DECISIONS.md sección 31): los objetivos son de
cada movimiento. SavingsService solo usa eliminar_por_objetivo() /
eliminar_por_activo(), al borrar un objetivo (sección 32) o un instrumento
(sección 35): la FK lo exige.

Sin lógica de negocio: no decide cuándo se puede asignar ni reparte montos
— eso vive en services/savings_service.py. validar_porcentajes() solo
consulta la suma (la regla "<= 100" no entra en un CHECK de SQLite: ver el
comentario de la tabla en db/schema.sql); quien escribe la llama dentro de
su propia transacción y decide qué hacer.

Las lecturas traen los nombres (objetivo_nombre / activo_nombre) para no
tener que resolverlos fila por fila. crear()/actualizar_porcentaje()/
eliminar() aceptan `conn` para participar de una transacción externa.
"""

import sqlite3
from typing import Optional

from db.database import DatabaseManager
from repositories._ids import nuevo_id

# Lectura enriquecida: columnas propias + nombre del objetivo y del activo.
_SELECT = """
    SELECT ao.*, o.nombre AS objetivo_nombre, a.nombre AS activo_nombre
    FROM activo_objetivos ao
    JOIN objetivos_ahorro o ON o.id = ao.objetivo_id
    JOIN activos_financieros a ON a.id = ao.activo_id
"""
# Suma de porcentajes que se considera <= 100 (REAL: 33.33 + 33.33 + 33.34).
TOLERANCIA_PORCENTAJE = 1e-6


class ActivoObjetivosRepository:
    def __init__(self, db: DatabaseManager):
        self._db = db

    def _ejecutar(self, sql: str, params: tuple, conn: Optional[sqlite3.Connection]) -> sqlite3.Cursor:
        if conn is not None:
            return conn.execute(sql, params)
        cursor = self._db.conn.execute(sql, params)
        self._db.conn.commit()
        return cursor

    # ----------------------------------------------------------
    # CREATE / UPDATE / DELETE
    # ----------------------------------------------------------

    def crear(self, activo_id: str, objetivo_id: str, porcentaje: float, conn: Optional[sqlite3.Connection] = None) -> str:
        """Inserta el reparto de un objetivo en un activo y devuelve su id. UNIQUE(activo_id, objetivo_id)."""
        reparto_id = nuevo_id()
        self._ejecutar(
            "INSERT INTO activo_objetivos (id, activo_id, objetivo_id, porcentaje) VALUES (?, ?, ?, ?);",
            (reparto_id, activo_id, objetivo_id, porcentaje), conn,
        )
        return reparto_id

    def actualizar_porcentaje(self, reparto_id: str, porcentaje: float, conn: Optional[sqlite3.Connection] = None) -> bool:
        cursor = self._ejecutar("UPDATE activo_objetivos SET porcentaje = ? WHERE id = ?;", (porcentaje, reparto_id), conn)
        return cursor.rowcount > 0

    def eliminar(self, reparto_id: str, conn: Optional[sqlite3.Connection] = None) -> bool:
        cursor = self._ejecutar("DELETE FROM activo_objetivos WHERE id = ?;", (reparto_id,), conn)
        return cursor.rowcount > 0

    def eliminar_por_objetivo(self, objetivo_id: str, conn: Optional[sqlite3.Connection] = None) -> int:
        """Todas las filas de un objetivo (antes de borrarlo: FK). Devuelve cuántas borró."""
        cursor = self._ejecutar("DELETE FROM activo_objetivos WHERE objetivo_id = ?;", (objetivo_id,), conn)
        return cursor.rowcount

    def eliminar_por_activo(self, activo_id: str, conn: Optional[sqlite3.Connection] = None) -> int:
        """Todas las filas de un activo (antes de borrarlo: FK). Devuelve cuántas borró."""
        cursor = self._ejecutar("DELETE FROM activo_objetivos WHERE activo_id = ?;", (activo_id,), conn)
        return cursor.rowcount

    # ----------------------------------------------------------
    # READ
    # ----------------------------------------------------------

    def listar(self) -> list[sqlite3.Row]:
        """Todos los repartos (para armar resúmenes sin una consulta por activo)."""
        return self._db.fetchall(_SELECT + " ORDER BY a.nombre, ao.porcentaje DESC, o.nombre;")

    def listar_por_activo(self, activo_id: str) -> list[sqlite3.Row]:
        return self._db.fetchall(_SELECT + " WHERE ao.activo_id = ? ORDER BY ao.porcentaje DESC, o.nombre;", (activo_id,))

    def listar_por_objetivo(self, objetivo_id: str) -> list[sqlite3.Row]:
        return self._db.fetchall(_SELECT + " WHERE ao.objetivo_id = ? ORDER BY a.nombre;", (objetivo_id,))

    def obtener(self, activo_id: str, objetivo_id: str) -> Optional[sqlite3.Row]:
        return self._db.fetchone(_SELECT + " WHERE ao.activo_id = ? AND ao.objetivo_id = ?;", (activo_id, objetivo_id))

    def validar_porcentajes(self, activo_id: str) -> bool:
        """True si la suma de porcentajes del activo no supera 100 (lee la conexión de la app: ve lo no comiteado)."""
        fila = self._db.fetchone(
            "SELECT COALESCE(SUM(porcentaje), 0) AS total FROM activo_objetivos WHERE activo_id = ?;", (activo_id,),
        )
        return fila["total"] <= 100 + TOLERANCIA_PORCENTAJE
