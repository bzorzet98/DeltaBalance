"""
DeltaBalance — services/categorias_service.py

Purpose:
    Gestión completa de categorías (crear/editar/desactivar/activar) más la
    exposición de solo lectura que ya existía (list_categories()/
    get_category()) para poblar selectores de categoría en el resto de la
    UI. Antes de esta tarea el service era puramente de lectura — ver
    AUDITORÍA abajo para el motivo de por qué algunas categorías no se
    pueden editar/desactivar libremente.

AUDITORÍA (grep de services/, repositories/, ui/ — no hay ningún lugar que
haga hoy un `WHERE subcategoria = '...'` ni una comparación de string en
tiempo de ejecución dentro de esas tres carpetas; todo pasa por
categoria_id). Sin embargo, dos categorías tienen una convención de nombre
documentada de la que dependen flujos reales, y ambas se buscan por nombre
literal desde tests/verify — renombrarlas o desactivarlas rompería esas
cosas en silencio:

1. INGRESOS · Sueldo — services/empleos_service.py (docstring de
   create_receipt(), línea ~240) documenta que quien llama debe pasar el
   categoria_id de 'Sueldo' al vincular un recibo de sueldo con una
   transacción de ingreso (el método no la busca por nombre en runtime, la
   resolución queda a cargo del caller). tests/conftest.py y
   verify/presupuestos_ingresos_empleos/verify_empleos_repository.py +
   verify_empleos_service.py la resuelven con
   "WHERE subcategoria = 'Sueldo'" literal.
2. MOVIMIENTO CAPITAL · Autotransferencia — services/transaction_service.py
   (docstring de create_transfer(), línea ~369) documenta
   "category_id: Should be the 'Autotransferencia' category" como el valor
   esperado que cualquier UI de transferencias entre cuentas debe pasar.
   tests/conftest.py la resuelve con
   "WHERE subcategoria = 'Autotransferencia'" literal.

Otras categorías aparecen hardcodeadas en tests/conftest.py y en
verify/dashboard/verify_dashboard_service.py (Supermercado, Salud,
Alquiler / Vivienda, Educacion, Hogar: Mantenimiento) pero solo como datos
de ejemplo para armar un escenario realista — ningún service branch según
su nombre exacto, así que no se consideran protegidas.

CATEGORIAS_PROTEGIDAS queda como constante de módulo, con motivo explícito
por entrada, para que quien la lea entienda por qué sin tener que releer
esta auditoría.
"""

import sqlite3
from dataclasses import dataclass, field
from typing import Any, Optional

from db.database import DatabaseManager
from repositories.categorias_repository import CategoriasRepository
from repositories._sentinels import NO_CAMBIAR

TIPOS_VALIDOS = ("ingreso", "egreso", "movimiento")

# Clave: (categoria_principal, subcategoria) tal como están en la fila —
# se matchea por nombre, no por id, porque el id varía entre bases (dummy
# DB de verify/, DB real del usuario, etc.) pero el par de nombres es la
# identidad estable que describe la auditoría de arriba.
CATEGORIAS_PROTEGIDAS: dict[tuple[str, str], str] = {
    ("INGRESOS", "Sueldo"): (
        "EmpleosService espera esta categoría por convención documentada "
        "(no por id fijo) para vincular recibos de sueldo con transacciones "
        "de ingreso; tests/verify la buscan por este nombre exacto."
    ),
    ("MOVIMIENTO CAPITAL", "Autotransferencia"): (
        "TransactionService.create_transfer() documenta esta categoría como "
        "la esperada para autotransferencias entre cuentas; tests/verify la "
        "buscan por este nombre exacto."
    ),
}


# =============================================================
# EXCEPTIONS
# =============================================================

class CategoriasError(Exception):
    """Base exception for category-related domain errors."""


class CategoryNotFoundError(CategoriasError):
    def __init__(self, categoria_id: int):
        super().__init__(f"No existe una categoría con id={categoria_id}.")
        self.categoria_id = categoria_id


class CategoryProtegidaError(CategoriasError):
    def __init__(self, categoria_principal: str, subcategoria: str, motivo: str):
        super().__init__(
            f"La categoría '{categoria_principal} · {subcategoria}' está protegida "
            f"y no se puede editar ni desactivar. Motivo: {motivo}"
        )
        self.categoria_principal = categoria_principal
        self.subcategoria = subcategoria
        self.motivo = motivo


# =============================================================
# RESULT
# =============================================================

@dataclass
class CategoriasResult:
    """Structured result returned by CategoriasService write operations."""
    success:      bool
    categoria_id: Optional[int] = None
    data:         dict          = field(default_factory=dict)
    message:      str           = ""


class CategoriasService:
    """
    Usage:
        db  = DatabaseManager()
        svc = CategoriasService(db)

        svc.list_categories(tipo="egreso")
        svc.get_category(5)
        svc.create_category("EGRESOS VARIABLES", "Mascotas", "egreso")
        svc.update_category(5, subcategoria="Mascotas y Veterinaria")
        svc.deactivate_category(5)
        svc.activate_category(5)
    """

    def __init__(self, db: DatabaseManager):
        self._db = db
        self._repo = CategoriasRepository(db)

    # ----------------------------------------------------------
    # LECTURA
    # ----------------------------------------------------------

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

    # ----------------------------------------------------------
    # PROTECCIÓN
    # ----------------------------------------------------------

    @staticmethod
    def protection_reason(categoria: sqlite3.Row) -> Optional[str]:
        """Motivo de protección de `categoria`, o None si no está protegida."""
        return CATEGORIAS_PROTEGIDAS.get(
            (categoria["categoria_principal"], categoria["subcategoria"])
        )

    @staticmethod
    def is_protected(categoria: sqlite3.Row) -> bool:
        return CategoriasService.protection_reason(categoria) is not None

    def _obtener_o_lanzar(self, categoria_id: int) -> sqlite3.Row:
        fila = self._repo.obtener_por_id(categoria_id)
        if fila is None:
            raise CategoryNotFoundError(categoria_id)
        return fila

    def _asegurar_no_protegida(self, categoria: sqlite3.Row) -> None:
        motivo = self.protection_reason(categoria)
        if motivo is not None:
            raise CategoryProtegidaError(
                categoria["categoria_principal"], categoria["subcategoria"], motivo
            )

    # ----------------------------------------------------------
    # ESCRITURA
    # ----------------------------------------------------------

    def create_category(
        self, categoria_principal: str, subcategoria: str, tipo: str
    ) -> CategoriasResult:
        """
        Creates a new category.

        Raises:
            CategoriasError if categoria_principal/subcategoria are empty,
            tipo is not one of TIPOS_VALIDOS, or the (categoria_principal,
            subcategoria) pair already exists (schema UNIQUE constraint).
        """
        if not categoria_principal or not categoria_principal.strip():
            raise CategoriasError("categoria_principal no puede estar vacío.")
        if not subcategoria or not subcategoria.strip():
            raise CategoriasError("subcategoria no puede estar vacío.")
        if tipo not in TIPOS_VALIDOS:
            raise CategoriasError(
                f"tipo inválido: {tipo!r}. Debe ser uno de {TIPOS_VALIDOS}."
            )

        principal = categoria_principal.strip()
        sub = subcategoria.strip()
        try:
            categoria_id = self._repo.crear(principal, sub, tipo)
        except sqlite3.IntegrityError as err:
            raise CategoriasError(
                f"Ya existe una categoría '{principal} · {sub}'."
            ) from err

        return CategoriasResult(
            success=True,
            categoria_id=categoria_id,
            data=dict(self._repo.obtener_por_id(categoria_id)),
            message=f"Categoría '{principal} · {sub}' creada.",
        )

    def update_category(
        self,
        categoria_id: int,
        categoria_principal: Any = NO_CAMBIAR,
        subcategoria: Any = NO_CAMBIAR,
        tipo: Any = NO_CAMBIAR,
    ) -> CategoriasResult:
        """
        Updates editable fields of a category. Default NO_CAMBIAR = no tocar
        ese campo (repositories/_sentinels.py).

        Raises:
            CategoryNotFoundError si categoria_id no existe.
            CategoryProtegidaError si la categoría está en CATEGORIAS_PROTEGIDAS
            (bloquea la edición completa, no campo por campo — renombrar una
            protegida la sacaría igual de la convención documentada que la
            protege).
            CategoriasError si el resultado final queda con campos vacíos,
            tipo inválido, o colisiona con otra categoría ya existente.
        """
        fila = self._obtener_o_lanzar(categoria_id)
        self._asegurar_no_protegida(fila)

        principal_final = (
            fila["categoria_principal"] if categoria_principal is NO_CAMBIAR else categoria_principal
        )
        sub_final = fila["subcategoria"] if subcategoria is NO_CAMBIAR else subcategoria
        tipo_final = fila["tipo"] if tipo is NO_CAMBIAR else tipo

        if not principal_final or not principal_final.strip():
            raise CategoriasError("categoria_principal no puede estar vacío.")
        if not sub_final or not sub_final.strip():
            raise CategoriasError("subcategoria no puede estar vacío.")
        if tipo_final not in TIPOS_VALIDOS:
            raise CategoriasError(
                f"tipo inválido: {tipo_final!r}. Debe ser uno de {TIPOS_VALIDOS}."
            )

        principal_final = principal_final.strip()
        sub_final = sub_final.strip()

        try:
            self._repo.actualizar(
                categoria_id,
                categoria_principal=principal_final,
                subcategoria=sub_final,
                tipo=tipo_final,
            )
        except sqlite3.IntegrityError as err:
            raise CategoriasError(
                f"Ya existe una categoría '{principal_final} · {sub_final}'."
            ) from err

        return CategoriasResult(
            success=True,
            categoria_id=categoria_id,
            data=dict(self._repo.obtener_por_id(categoria_id)),
            message=f"Categoría '{principal_final} · {sub_final}' actualizada.",
        )

    def deactivate_category(self, categoria_id: int) -> CategoriasResult:
        """
        Soft-delete (activa = 0). A diferencia de AccountsService.archive_account(),
        NO exige uso/saldo cero — el soft-delete de categorías ya está pensado
        para convivir con historial existente (ver
        docs/DATA_MODEL_DECISIONS.md sección 11: filtrar categorías inactivas
        en los listados es responsabilidad de quien lista, no una condición
        para poder desactivar).

        Raises:
            CategoryNotFoundError si categoria_id no existe.
            CategoryProtegidaError si la categoría está en CATEGORIAS_PROTEGIDAS.
        """
        fila = self._obtener_o_lanzar(categoria_id)
        self._asegurar_no_protegida(fila)

        self._repo.desactivar(categoria_id)
        return CategoriasResult(
            success=True,
            categoria_id=categoria_id,
            data=dict(self._repo.obtener_por_id(categoria_id)),
            message=f"Categoría '{fila['categoria_principal']} · {fila['subcategoria']}' desactivada.",
        )

    def activate_category(self, categoria_id: int) -> CategoriasResult:
        """
        Reactiva una categoría desactivada. Sin restricción de protección —
        reactivar siempre es seguro, nunca rompe la convención de nombre que
        protege a una categoría (al contrario, la deja disponible de nuevo).

        Raises:
            CategoryNotFoundError si categoria_id no existe.
        """
        fila = self._obtener_o_lanzar(categoria_id)
        self._repo.activar(categoria_id)
        return CategoriasResult(
            success=True,
            categoria_id=categoria_id,
            data=dict(self._repo.obtener_por_id(categoria_id)),
            message=f"Categoría '{fila['categoria_principal']} · {fila['subcategoria']}' reactivada.",
        )
