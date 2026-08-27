"""
DeltaBalance — services/presupuestos_service.py

Purpose:
    Domain service for expense budgets (presupuestos). Owns the business
    rules around setting/copying budgets — data access lives entirely in
    PresupuestosRepository.

    Fase 2, bloque PRESUPUESTOS, paso 2a: este service se crea DESDE CERO.
    No existe ningún PresupuestosService previo ni en db/database.py ni en
    ningún otro lugar — no hay comportamiento anterior que replicar, el
    diseño sale directo del repositorio (paso 1) y del schema.

    Does NOT recalculate monto_ejecutado_minor summing real transactions —
    ver docstring de update_executed().
"""

import sqlite3
from dataclasses import dataclass, field
from typing import Optional

from db.database import DatabaseManager
from repositories.presupuestos_repository import PresupuestosRepository
from repositories.categorias_repository import CategoriasRepository

# =============================================================
# EXCEPTIONS
# =============================================================

class PresupuestoError(Exception):
    """Raised when a budget operation violates a business rule."""


class CategoryNotFoundError(PresupuestoError):
    """
    Raised when a referenced category does not exist.

    services/transaction_service.py ya tiene una CategoryNotFoundError
    (TransactionError) para el mismo concepto — se evaluó reusarla, pero
    importar una excepción de TransactionService acoplaría
    PresupuestosService a la jerarquía de excepciones de otro dominio
    (CLAUDE.md: "alta cohesión, bajo acoplamiento" — dos services de
    dominios distintos no deberían depender el uno del otro para algo tan
    básico como reportar un error). Se define acá una clase propia, con el
    mismo nombre por consistencia conceptual, pero como excepción
    independiente que cuelga de PresupuestoError.
    """


class CurrencyNotFoundError(PresupuestoError):
    """Raised when a referenced currency does not exist."""


# =============================================================
# RESULT TYPE
# =============================================================

@dataclass
class PresupuestoResult:
    """Structured result returned by PresupuestosService operations."""
    success:   bool
    entity_id: Optional[int] = None
    data:      dict          = field(default_factory=dict)
    message:   str           = ""


# =============================================================
# SERVICE
# =============================================================

class PresupuestosService:
    """
    Entry point for budget operations (set/get/list/copy).

    Usage:
        db  = DatabaseManager()
        svc = PresupuestosService(db)

        svc.set_budget(
            categoria_id=8, mes=5, anio=2026, moneda_id=1,
            monto_estimado_minor=50000, es_recurrente=True,
        )
    """

    def __init__(self, db: DatabaseManager):
        self._db = db
        self._repo = PresupuestosRepository(db)
        self._categorias_repo = CategoriasRepository(db)

    # ----------------------------------------------------------
    # INTERNAL HELPERS
    # ----------------------------------------------------------

    def _get_category(self, category_id: int) -> sqlite3.Row:
        """
        Fetches a category by id. Raises CategoryNotFoundError if not
        found. No MonedasRepository exists yet in this codebase (mismo
        estado que en transaction_service.py/fees_service.py, que también
        consultan `monedas` directo) — _get_currency() de acá hace lo
        mismo.
        """
        row = self._categorias_repo.obtener_por_id(category_id)
        if row is None:
            raise CategoryNotFoundError(f"Category id={category_id} not found.")
        return row

    def _get_currency(self, currency_id: int) -> sqlite3.Row:
        """Fetches a currency by id. Raises CurrencyNotFoundError if not found."""
        row = self._db.fetchone("SELECT * FROM monedas WHERE id = ?;", (currency_id,))
        if row is None:
            raise CurrencyNotFoundError(f"Currency id={currency_id} not found.")
        return row

    # ----------------------------------------------------------
    # SET BUDGET
    # ----------------------------------------------------------

    def set_budget(
        self,
        categoria_id: int,
        mes: int,
        anio: int,
        moneda_id: int,
        monto_estimado_minor: int,
        es_recurrente: bool = False,
        notas: Optional[str] = None,
        formula_estimado: Optional[str] = None,
    ) -> PresupuestoResult:
        """
        Crea o actualiza el presupuesto de una categoría para un mes/año
        (INSERT ... ON CONFLICT DO UPDATE sobre el UNIQUE(categoria_id,
        mes, anio) — ver PresupuestosRepository.upsert()).

        Args:
            categoria_id:          Debe existir (CategoryNotFoundError si no).
            mes:                   1-12 (ValueError si no).
            anio:                  Año del presupuesto.
            moneda_id:              Debe existir (CurrencyNotFoundError si no).
            monto_estimado_minor:  Debe ser > 0 (ValueError si no) — el
                                   RESULTADO ya calculado, sea de una
                                   fórmula o de un número directo. Este
                                   service no evalúa fórmulas (eso vive en
                                   utils/calculadora_segura.py, capa de
                                   ui/) — solo persiste el par
                                   (resultado, fórmula que lo produjo).
            es_recurrente:         Si este presupuesto se copia por default
                                   en copy_period(solo_recurrentes=True).
            notas:                 Nota libre opcional.
            formula_estimado:      Texto de la fórmula que produjo
                                   monto_estimado_minor (con el "="
                                   incluido), o None si se cargó como
                                   número directo. SIEMPRE se reescribe en
                                   el camino UPDATE (pasthrough directo de
                                   PresupuestosRepository.upsert(), sin
                                   sentinel de "no tocar") — pasar None a
                                   propósito sobre un presupuesto que antes
                                   tenía fórmula la limpia a NULL, para no
                                   dejar una fórmula vieja asociada a un
                                   monto que ya no le corresponde.

        Returns:
            PresupuestoResult con los datos del presupuesto seteado.

        Raises:
            ValueError si mes está fuera de rango o monto_estimado_minor <= 0.
            CategoryNotFoundError si categoria_id no existe.
            CurrencyNotFoundError si moneda_id no existe.
        """
        if not (1 <= mes <= 12):
            raise ValueError(f"Month must be between 1 and 12. Received: {mes}.")
        if monto_estimado_minor <= 0:
            raise ValueError(
                f"monto_estimado_minor must be positive. Received: {monto_estimado_minor}."
            )

        self._get_category(categoria_id)   # validate existence
        self._get_currency(moneda_id)      # validate existence

        filas_afectadas = self._repo.upsert(
            categoria_id=categoria_id,
            mes=mes,
            anio=anio,
            moneda_id=moneda_id,
            monto_estimado_minor=monto_estimado_minor,
            es_recurrente=es_recurrente,
            notas=notas,
            formula_estimado=formula_estimado,
        )

        return PresupuestoResult(
            success=True,
            data={
                "categoria_id":          categoria_id,
                "mes":                   mes,
                "anio":                  anio,
                "moneda_id":             moneda_id,
                "monto_estimado_minor":  monto_estimado_minor,
                "es_recurrente":         es_recurrente,
                "formula_estimado":      formula_estimado,
                "filas_afectadas":       filas_afectadas,
            },
            message=f"Budget set for category {categoria_id} on {mes:02d}/{anio}.",
        )

    # ----------------------------------------------------------
    # READ
    # ----------------------------------------------------------

    def get_budget(self, categoria_id: int, mes: int, anio: int) -> Optional[sqlite3.Row]:
        """Fetches a single budget by its natural key (categoria_id, mes, anio)."""
        return self._repo.obtener_por_periodo(categoria_id, mes, anio)

    def list_budgets(self, mes: int, anio: int) -> list[sqlite3.Row]:
        """
        Lists all budgets for a month/year, enriched with
        subcategoria/categoria_principal/moneda_codigo (JOIN ya resuelto
        en PresupuestosRepository.listar_por_periodo()).
        """
        return self._repo.listar_por_periodo(mes, anio)

    def list_budgeted_category_ids(self) -> list[int]:
        """
        IDs de categorías con al menos un presupuesto cargado alguna vez,
        en cualquier período — pasthrough directo de
        PresupuestosRepository.listar_categoria_ids_con_presupuesto(). Sin
        lógica de negocio propia: filtrar SOLO las de tipo='egreso' o
        combinar con las agregadas a mano en la sesión es responsabilidad
        de quien consume esto (ui/screens/presupuestos.py).
        """
        return self._repo.listar_categoria_ids_con_presupuesto()

    # ----------------------------------------------------------
    # UPDATE EXECUTED
    # ----------------------------------------------------------

    def update_executed(
        self,
        categoria_id: int,
        mes: int,
        anio: int,
        monto_ejecutado_minor: int,
    ) -> PresupuestoResult:
        """
        Escribe monto_ejecutado_minor YA CALCULADO por el caller.

        NO recalcula sumando transacciones reales de esa categoría/mes/año
        (CLAUDE.md sección 1: "monto_ejecutado_minor se actualiza
        automáticamente sumando transacciones de esa categoría/mes — nunca
        se carga a mano en dos lugares"). Esa suma automática es una fase
        futura, cuando este service se conecte con TransaccionesRepository
        para calcular el ejecutado real — no implementada acá a propósito,
        para no anticiparse a un diseño que todavía no se definió (filtros
        de fecha exactos dentro del mes, qué transacciones cuentan, etc.).
        Por ahora este método es un passthrough simple hacia
        PresupuestosRepository.actualizar_ejecutado().
        """
        self._repo.actualizar_ejecutado(categoria_id, mes, anio, monto_ejecutado_minor)
        return PresupuestoResult(
            success=True,
            data={
                "categoria_id":            categoria_id,
                "mes":                     mes,
                "anio":                    anio,
                "monto_ejecutado_minor":   monto_ejecutado_minor,
            },
            message=f"Executed amount updated for category {categoria_id} on {mes:02d}/{anio}.",
        )

    # ----------------------------------------------------------
    # COPY PERIOD
    # ----------------------------------------------------------

    def copy_period(
        self,
        mes_origen: int,
        anio_origen: int,
        mes_destino: int,
        anio_destino: int,
        solo_recurrentes: bool = True,
    ) -> PresupuestoResult:
        """
        Copia los presupuestos de un período a otro.

        DECISIÓN DE DISEÑO — el filtro "solo recurrentes" vive ACÁ, no en
        el repositorio: PresupuestosRepository.copiar_periodo() copia TODO
        el período tal cual, sin conocer el concepto de "recurrente"; qué
        significa "copiar solo lo recurrente" es una regla de negocio
        (CLAUDE.md sección 3: "Sin lógica de negocio adentro" para
        repositorios). Se optó por NO agregar un método nuevo al
        repositorio (ej. copiar_periodo_recurrentes()) — en vez de eso,
        este método lista el período origen con listar_por_periodo(),
        filtra en Python por es_recurrente == 1, y llama a
        PresupuestosRepository.upsert() una fila a la vez para cada
        presupuesto recurrente, dentro de una única transacción
        (self._db.transaction()) para que la copia sea atómica igual que
        copiar_periodo() del repositorio (que comitea una sola vez al
        final, no fila por fila).

        Detalle importante para no romper la semántica de "copiar" en el
        camino filtrado: copiar_periodo() del repositorio usa INSERT OR
        IGNORE (nunca sobreescribe un presupuesto que ya existe en el
        destino). upsert() en cambio SIEMPRE sobreescribe en conflicto. Para
        que el camino solo_recurrentes=True tenga el mismo comportamiento
        de "no pisar lo que ya existe" que el camino sin filtro, este
        método chequea obtener_por_periodo() antes de cada upsert() y
        omite la fila si el destino ya tiene un presupuesto para esa
        categoría — nunca llama a upsert() sobre una fila que ya existe.

        Args:
            mes_origen/anio_origen:   Período de origen.
            mes_destino/anio_destino: Período de destino.
            solo_recurrentes:         Si True (default), solo copia los
                                      presupuestos marcados es_recurrente=1.
                                      Si False, copia todo el período tal
                                      cual vía
                                      PresupuestosRepository.copiar_periodo().

        Returns:
            PresupuestoResult con la cantidad de presupuestos copiados.
        """
        if not solo_recurrentes:
            copiados = self._repo.copiar_periodo(mes_origen, anio_origen, mes_destino, anio_destino)
            return PresupuestoResult(
                success=True,
                data={"copiados": copiados, "solo_recurrentes": False},
                message=(
                    f"{copiados} budget(s) copied from {mes_origen:02d}/{anio_origen} "
                    f"to {mes_destino:02d}/{anio_destino}."
                ),
            )

        presupuestos_origen = self._repo.listar_por_periodo(mes_origen, anio_origen)
        recurrentes = [p for p in presupuestos_origen if p["es_recurrente"] == 1]

        conn = self._db.conn
        copiados = 0
        with self._db.transaction():
            for presupuesto in recurrentes:
                ya_existe = self._repo.obtener_por_periodo(
                    presupuesto["categoria_id"], mes_destino, anio_destino
                )
                if ya_existe is not None:
                    continue
                self._repo.upsert(
                    categoria_id=presupuesto["categoria_id"],
                    mes=mes_destino,
                    anio=anio_destino,
                    moneda_id=presupuesto["moneda_id"],
                    monto_estimado_minor=presupuesto["monto_estimado_minor"],
                    es_recurrente=True,
                    notas=presupuesto["notas"],
                    conn=conn,
                )
                copiados += 1

        return PresupuestoResult(
            success=True,
            data={"copiados": copiados, "solo_recurrentes": True},
            message=(
                f"{copiados} recurring budget(s) copied from {mes_origen:02d}/{anio_origen} "
                f"to {mes_destino:02d}/{anio_destino}."
            ),
        )
