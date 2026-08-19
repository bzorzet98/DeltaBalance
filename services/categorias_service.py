"""
DeltaBalance — services/categorias_service.py

Purpose:
    Exposición mínima de solo lectura de CategoriasRepository para que la UI
    pueda poblar selectores de categoría sin tocar repositories/ directo
    (CLAUDE.md §2/§3). No hay CategoriasService previo en el proyecto — se
    crea acá siguiendo el mismo patrón mínimo que AccountsService, pero
    reducido a listar()/obtener_por_id(): categorías todavía no se
    gestionan (crear/editar/desactivar) desde la UI, solo se consultan para
    los formularios de transacciones/compras en cuotas (Fase 5, flujo del
    botón "+" del dashboard). Cuando ese día llegue (gestión real de
    categorías desde la UI), acá es donde va a vivir la regla de negocio de
    "no desactivar una categoría con transacciones asociadas" que hoy
    CategoriasRepository.desactivar() deliberadamente no aplica (ver su
    docstring y docs/DATA_MODEL_DECISIONS.md sección 11).
"""

import sqlite3
from typing import Optional

from db.database import DatabaseManager
from repositories.categorias_repository import CategoriasRepository


class CategoriasService:
    """
    Entry point de solo lectura para categorías.

    Usage:
        db  = DatabaseManager()
        svc = CategoriasService(db)

        svc.list_categories(tipo="egreso")
        svc.get_category(5)
    """

    def __init__(self, db: DatabaseManager):
        self._db = db
        self._repo = CategoriasRepository(db)

    def list_categories(
        self, tipo: Optional[str] = None, incluir_inactivas: bool = False
    ) -> list[sqlite3.Row]:
        """
        Lists categories, optionally filtered by tipo ('ingreso', 'egreso',
        'movimiento'). Excludes inactive (soft-deleted) categories by default.
        """
        return self._repo.listar(tipo=tipo, incluir_inactivas=incluir_inactivas)

    def get_category(self, categoria_id: int) -> Optional[sqlite3.Row]:
        """Fetches a single category by id, or None if it does not exist."""
        return self._repo.obtener_por_id(categoria_id)
