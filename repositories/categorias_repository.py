"""
DeltaBalance — repositories/categorias_repository.py

Acceso a datos para la tabla `categorias`. Sin lógica de negocio: no bloquea
el soft-delete aunque la categoría tenga transacciones asociadas — esa regla
vive en un futuro CategoriasService, no acá (ver
docs/DATA_MODEL_DECISIONS.md sección 11).

Reemplaza a los métodos de categorías de db/database.py::DatabaseManager
(obtener_categorias, obtener_categoria, crear_categoria), que quedan
deprecados ahí hasta que services/ migre a usar este repositorio.
"""

import sqlite3
from typing import Optional

from db.database import DatabaseManager


class CategoriasRepository:
    def __init__(self, db: DatabaseManager):
        self._db = db

    def crear(self, categoria_principal: str, subcategoria: str, tipo: str) -> int:
        return self._db.execute(
            "INSERT INTO categorias (categoria_principal, subcategoria, tipo) VALUES (?, ?, ?);",
            (categoria_principal, subcategoria, tipo),
        )

    def obtener_por_id(self, categoria_id: int) -> Optional[sqlite3.Row]:
        return self._db.fetchone("SELECT * FROM categorias WHERE id = ?;", (categoria_id,))

    def actualizar(
        self,
        categoria_id: int,
        categoria_principal: Optional[str] = None,
        subcategoria: Optional[str] = None,
        tipo: Optional[str] = None,
    ) -> None:
        """
        Actualiza campos editables. None = no tocar ese campo — mismo
        criterio que CuentasRepository.actualizar(). Sin lógica de negocio:
        no valida si la categoría está protegida (eso vive en el service).
        """
        campos, valores = [], []
        if categoria_principal is not None:
            campos.append("categoria_principal = ?")
            valores.append(categoria_principal)
        if subcategoria is not None:
            campos.append("subcategoria = ?")
            valores.append(subcategoria)
        if tipo is not None:
            campos.append("tipo = ?")
            valores.append(tipo)
        if not campos:
            return
        valores.append(categoria_id)
        self._db.execute(f"UPDATE categorias SET {', '.join(campos)} WHERE id = ?;", tuple(valores))

    def listar(
        self, tipo: Optional[str] = None, incluir_inactivas: bool = False
    ) -> list[sqlite3.Row]:
        condiciones, params = [], []
        if not incluir_inactivas:
            condiciones.append("activa = 1")
        if tipo:
            condiciones.append("tipo = ?")
            params.append(tipo)

        sql = "SELECT * FROM categorias"
        if condiciones:
            sql += " WHERE " + " AND ".join(condiciones)
        sql += " ORDER BY categoria_principal, subcategoria;"
        return self._db.fetchall(sql, tuple(params))

    def desactivar(self, categoria_id: int) -> None:
        """
        Soft-delete: activa = 0. NO valida si la categoría tiene
        transacciones asociadas — esa regla de negocio vive en el service.
        """
        self._db.execute("UPDATE categorias SET activa = 0 WHERE id = ?;", (categoria_id,))

    def activar(self, categoria_id: int) -> None:
        self._db.execute("UPDATE categorias SET activa = 1 WHERE id = ?;", (categoria_id,))
