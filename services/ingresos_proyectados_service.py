"""
DeltaBalance — services/ingresos_proyectados_service.py

Purpose:
    Domain service for projected income (ingresos_proyectados). Owns the
    business rules around creating/updating/collecting projected income —
    data access lives entirely in IngresosProyectadosRepository.

    Fase 2, bloque INGRESOS PROYECTADOS, paso 2b: este service se crea
    DESDE CERO. No existe ningún IngresosProyectadosService previo ni en
    db/database.py ni en ningún otro lugar — no hay comportamiento anterior
    que replicar, el diseño sale directo del repositorio (paso 1) y del
    schema.
"""

import sqlite3
from dataclasses import dataclass, field
from typing import Any, Optional

from db.database import DatabaseManager
from repositories.ingresos_proyectados_repository import IngresosProyectadosRepository
from repositories._sentinels import NO_CAMBIAR

# =============================================================
# EXCEPTIONS
# =============================================================

class IngresoProyectadoError(Exception):
    """Raised when a projected income operation violates a business rule."""


class IngresoProyectadoNotFoundError(IngresoProyectadoError):
    """Raised when a referenced projected income does not exist."""


class CurrencyNotFoundError(IngresoProyectadoError):
    """
    Raised when a referenced currency does not exist.

    Definida propia de este módulo, colgando de IngresoProyectadoError —
    mismo razonamiento que en services/presupuestos_service.py: importar la
    CurrencyNotFoundError de otro service acoplaría este dominio al de otro,
    para algo tan básico como reportar un error (CLAUDE.md: "alta cohesión,
    bajo acoplamiento").
    """


class IngresoYaCobradoError(IngresoProyectadoError):
    """
    Raised when attempting to change the state of a projected income that
    is already 'cobrado' (mark_partial()/mark_collected()). Ver punto 7 del
    resumen de esta tarea para el razonamiento completo — resumen: 'cobrado'
    es un estado terminal, la corrección posterior a cerrarlo es vía
    update() de los campos base, no volver a transicionar el estado.
    """


# =============================================================
# RESULT TYPE
# =============================================================

@dataclass
class IngresoProyectadoResult:
    """Structured result returned by IngresosProyectadosService operations."""
    success:   bool
    entity_id: Optional[int] = None
    data:      dict          = field(default_factory=dict)
    message:   str           = ""


# =============================================================
# SERVICE
# =============================================================

class IngresosProyectadosService:
    """
    Entry point for projected income operations (create/get/list/update/
    mark_partial/mark_collected).

    Usage:
        db  = DatabaseManager()
        svc = IngresosProyectadosService(db)

        svc.create(
            concepto="Freelance", mes=5, anio=2026,
            monto_estimado_minor=200000, moneda_id=1,
        )
    """

    def __init__(self, db: DatabaseManager):
        self._db = db
        self._repo = IngresosProyectadosRepository(db)

    # ----------------------------------------------------------
    # INTERNAL HELPERS
    # ----------------------------------------------------------

    def _get_ingreso(self, ingreso_id: int) -> sqlite3.Row:
        row = self._repo.obtener_por_id(ingreso_id)
        if row is None:
            raise IngresoProyectadoNotFoundError(f"Ingreso proyectado id={ingreso_id} not found.")
        return row

    def _get_currency(self, currency_id: int) -> sqlite3.Row:
        """
        Fetches a currency by id. No MonedasRepository exists yet in this
        codebase (mismo estado que en transaction_service.py/
        fees_service.py/presupuestos_service.py, que también consultan
        `monedas` directo).
        """
        row = self._db.fetchone("SELECT * FROM monedas WHERE id = ?;", (currency_id,))
        if row is None:
            raise CurrencyNotFoundError(f"Currency id={currency_id} not found.")
        return row

    # ----------------------------------------------------------
    # CREATE
    # ----------------------------------------------------------

    def create(
        self,
        concepto: str,
        mes: int,
        anio: int,
        monto_estimado_minor: int,
        moneda_id: int,
    ) -> IngresoProyectadoResult:
        """
        Creates a projected income entry. estado starts at 'pendiente'
        (column default, see IngresosProyectadosRepository.crear()).

        Raises:
            ValueError if concepto is empty, monto_estimado_minor <= 0, or
                       mes is out of range.
            CurrencyNotFoundError if moneda_id does not exist.
        """
        if not concepto or not concepto.strip():
            raise ValueError("Concept cannot be empty.")
        if monto_estimado_minor <= 0:
            raise ValueError(
                f"monto_estimado_minor must be positive. Received: {monto_estimado_minor}."
            )
        if not (1 <= mes <= 12):
            raise ValueError(f"Month must be between 1 and 12. Received: {mes}.")

        self._get_currency(moneda_id)  # validate existence

        ingreso_id = self._repo.crear(
            concepto=concepto.strip(),
            mes=mes,
            anio=anio,
            monto_estimado_minor=monto_estimado_minor,
            moneda_id=moneda_id,
        )

        return IngresoProyectadoResult(
            success=True,
            entity_id=ingreso_id,
            data={
                "concepto":              concepto.strip(),
                "mes":                   mes,
                "anio":                  anio,
                "monto_estimado_minor":  monto_estimado_minor,
                "moneda_id":             moneda_id,
            },
            message=f"Projected income '{concepto.strip()}' created for {mes:02d}/{anio}.",
        )

    # ----------------------------------------------------------
    # READ
    # ----------------------------------------------------------

    def get(self, ingreso_id: int) -> Optional[sqlite3.Row]:
        return self._repo.obtener_por_id(ingreso_id)

    def list_for_period(self, mes: int, anio: int) -> list[sqlite3.Row]:
        return self._repo.listar_por_periodo(mes, anio)

    # ----------------------------------------------------------
    # UPDATE
    # ----------------------------------------------------------

    def update(
        self,
        ingreso_id: int,
        concepto: Any = NO_CAMBIAR,
        monto_estimado_minor: Any = NO_CAMBIAR,
        moneda_id: Any = NO_CAMBIAR,
    ) -> IngresoProyectadoResult:
        """
        Update parcial de los campos base (concepto, monto_estimado_minor,
        moneda_id). Usa el sentinel NO_CAMBIAR de repositories/_sentinels.py
        directamente en la firma — el caller de este service usa el mismo
        sentinel que ya usan los repositorios, sin traducción intermedia a
        None, para consistencia en todo el proyecto.

        Disponible incluso sobre un ingreso ya 'cobrado' — a diferencia de
        mark_partial()/mark_collected() (bloqueados, ver
        IngresoYaCobradoError), update() de los campos base es justamente
        la vía de corrección para un ingreso ya cerrado (ej. arreglar un
        concepto mal tipeado). No toca `estado` ni `monto_percibido_minor`
        — esos son transición exclusiva de mark_partial()/mark_collected(),
        igual que IngresosProyectadosRepository.actualizar() los excluye a
        propósito.

        Raises:
            IngresoProyectadoNotFoundError si ingreso_id no existe.
            ValueError si concepto se pasa vacío o monto_estimado_minor se
                       pasa <= 0.
            CurrencyNotFoundError si moneda_id se pasa y no existe.
        """
        self._get_ingreso(ingreso_id)  # validate existence

        if concepto is not NO_CAMBIAR and (not concepto or not concepto.strip()):
            raise ValueError("Concept cannot be empty.")
        if monto_estimado_minor is not NO_CAMBIAR and monto_estimado_minor <= 0:
            raise ValueError(
                f"monto_estimado_minor must be positive. Received: {monto_estimado_minor}."
            )
        if moneda_id is not NO_CAMBIAR:
            self._get_currency(moneda_id)  # validate existence

        concepto_normalizado = concepto.strip() if concepto is not NO_CAMBIAR else NO_CAMBIAR

        actualizado = self._repo.actualizar(
            ingreso_id,
            concepto=concepto_normalizado,
            monto_estimado_minor=monto_estimado_minor,
            moneda_id=moneda_id,
        )

        return IngresoProyectadoResult(
            success=actualizado,
            entity_id=ingreso_id,
            data={
                "concepto":             concepto_normalizado if concepto_normalizado is not NO_CAMBIAR else None,
                "monto_estimado_minor": monto_estimado_minor if monto_estimado_minor is not NO_CAMBIAR else None,
                "moneda_id":            moneda_id if moneda_id is not NO_CAMBIAR else None,
            },
            message=(
                f"Projected income id={ingreso_id} updated."
                if actualizado else
                f"Projected income id={ingreso_id}: no fields to update."
            ),
        )

    # ----------------------------------------------------------
    # STATE TRANSITIONS
    # ----------------------------------------------------------

    def mark_partial(self, ingreso_id: int, monto_percibido_minor: int) -> IngresoProyectadoResult:
        """
        Transitions to estado='parcial'. Valid from 'pendiente' or another
        'parcial' (receiving income in more than one partial installment is
        normal — see punto 7 del resumen: solo 'cobrado' es terminal).

        Raises:
            IngresoProyectadoNotFoundError si ingreso_id no existe.
            IngresoYaCobradoError si el ingreso ya está 'cobrado'.
            ValueError si monto_percibido_minor <= 0, o si es >= al
                       monto_estimado_minor original (en ese caso corresponde
                       mark_collected(), no un cobro "parcial").
        """
        ingreso = self._get_ingreso(ingreso_id)
        if ingreso["estado"] == "cobrado":
            raise IngresoYaCobradoError(
                f"Ingreso proyectado id={ingreso_id} is already 'cobrado' — "
                f"state cannot be changed directly. Use update() to correct base fields."
            )
        if monto_percibido_minor <= 0:
            raise ValueError(
                f"monto_percibido_minor must be positive. Received: {monto_percibido_minor}."
            )
        if monto_percibido_minor >= ingreso["monto_estimado_minor"]:
            raise ValueError(
                f"monto_percibido_minor ({monto_percibido_minor}) must be less than "
                f"monto_estimado_minor ({ingreso['monto_estimado_minor']}) for a partial "
                f"receipt. If the full (or more than the) estimated amount was received, "
                f"use mark_collected() instead."
            )

        self._repo.marcar_estado(ingreso_id, "parcial", monto_percibido_minor=monto_percibido_minor)

        return IngresoProyectadoResult(
            success=True,
            entity_id=ingreso_id,
            data={"estado": "parcial", "monto_percibido_minor": monto_percibido_minor},
            message=f"Ingreso proyectado id={ingreso_id} marked as 'parcial' ({monto_percibido_minor} minor received).",
        )

    def mark_collected(
        self,
        ingreso_id: int,
        monto_percibido_minor: Optional[int] = None,
    ) -> IngresoProyectadoResult:
        """
        Transitions to estado='cobrado'. If monto_percibido_minor is not
        passed, uses monto_estimado_minor (received exactly as projected).
        If passed, it may legitimately differ from the estimate (received
        more or less than projected) — it just still closes the entry.

        Raises:
            IngresoProyectadoNotFoundError si ingreso_id no existe.
            IngresoYaCobradoError si el ingreso ya está 'cobrado'.
            ValueError si monto_percibido_minor se pasa y es <= 0.
        """
        ingreso = self._get_ingreso(ingreso_id)
        if ingreso["estado"] == "cobrado":
            raise IngresoYaCobradoError(
                f"Ingreso proyectado id={ingreso_id} is already 'cobrado' — "
                f"state cannot be changed directly. Use update() to correct base fields."
            )
        if monto_percibido_minor is not None and monto_percibido_minor <= 0:
            raise ValueError(
                f"monto_percibido_minor must be positive. Received: {monto_percibido_minor}."
            )

        monto_final = (
            monto_percibido_minor if monto_percibido_minor is not None
            else ingreso["monto_estimado_minor"]
        )
        self._repo.marcar_estado(ingreso_id, "cobrado", monto_percibido_minor=monto_final)

        return IngresoProyectadoResult(
            success=True,
            entity_id=ingreso_id,
            data={"estado": "cobrado", "monto_percibido_minor": monto_final},
            message=f"Ingreso proyectado id={ingreso_id} marked as 'cobrado' ({monto_final} minor received).",
        )
