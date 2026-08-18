"""
DeltaBalance — services/debts_service.py

Purpose:
    Manages all debt-related operations, covering both debts in your favor
    (someone owes you) and debts against you (you owe someone).

    Owns these tables exclusively:
        - deudas
        - deuda_pagos

    Does NOT create transactions. If a debt payment involves real cash movement,
    the caller (or OrchestrationService) is responsible for calling
    TransactionService.create() and passing the resulting transaction_id here.

    Key concepts:
        - tipo 'a_favor'   → someone owes YOU  (+)
        - tipo 'en_contra' → YOU owe someone   (-)
        - monto_original_minor  → the amount when the debt was created (never changes)
        - monto_pendiente_minor → decreases with each payment registered
        - estado transitions: activa → saldada (auto, when pendiente reaches 0)
                              activa → incobrable (manual write-off)
        - origen_tipo / origen_id → links a debt to its source (manual, transaccion,
          compra_cuotas) so OrchestrationService can trace what generated it
"""

import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional

from db.database import DatabaseManager, to_minor, from_minor
from db.query_builder import QueryBuilder
from repositories.deudas_repository import DeudasRepository


# =============================================================
# EXCEPTIONS
# =============================================================

class DebtError(Exception):
    """Raised when a debt operation violates a business rule."""


class DebtNotFoundError(DebtError):
    """Raised when a referenced debt does not exist."""


class DebtAlreadySettledError(DebtError):
    """Raised when attempting to modify a debt that is already settled or written off."""


class PaymentExceedsBalanceError(DebtError):
    """Raised when a payment amount exceeds the remaining pending balance."""


# =============================================================
# RESULT TYPE
# =============================================================

@dataclass
class DebtResult:
    """
    Structured result returned by DebtsService operations.
    Avoids forcing callers to make a second DB call to read what was just written.
    """
    success:  bool
    debt_id:  Optional[int]        = None
    data:     dict                 = field(default_factory=dict)
    message:  str                  = ""


# =============================================================
# SERVICE
# =============================================================

class DebtsService:
    """
    Entry point for all debt operations in DeltaBalance.

    Usage:
        db  = DatabaseManager()
        svc = DebtsService(db)

        # Someone owes you 5000 ARS
        result = svc.create(
            person="Noe",
            debt_type="a_favor",
            amount=5000.0,
            currency_code="ARS",
            date_str="2026-05-10",
            concept="Shared dinner",
        )

        # Register a partial payment
        svc.register_payment(debt_id=result.debt_id, amount=2000.0, currency_code="ARS",
                             date_str="2026-05-15")
    """

    def __init__(self, db: DatabaseManager):
        """
        Initializes the service with an active DatabaseManager instance.

        Args:
            db: An initialized DatabaseManager. Schema + seed must already be applied.
        """
        self._db = db
        self._repo = DeudasRepository(db)

    # ----------------------------------------------------------
    # INTERNAL HELPERS
    # ----------------------------------------------------------

    def _get_debt(self, debt_id: int) -> sqlite3.Row:
        """
        Fetches a debt row by ID.
        Raises DebtNotFoundError if it does not exist.

        Args:
            debt_id: Primary key of the deudas table.

        Returns:
            sqlite3.Row for the debt.
        """
        row = self._repo.obtener_por_id(debt_id)
        if row is None:
            raise DebtNotFoundError(f"Debt id={debt_id} not found.")
        return row

    def _get_currency(self, code: str) -> sqlite3.Row:
        """
        Fetches a currency row by code. Raises ValueError if not found.

        Args:
            code: Currency code. E.g. 'ARS', 'USD'.

        Returns:
            sqlite3.Row with id, codigo, decimales.
        """
        row = self._db.fetchone(
            "SELECT * FROM monedas WHERE codigo = ?;", (code.upper(),)
        )
        if row is None:
            raise ValueError(f"Currency '{code}' not found.")
        return row

    @staticmethod
    def _validate_date(date_str) -> str:
        """
        Validates and normalizes a date to 'YYYY-MM-DD' string format.
        Accepts date/datetime objects or strings.

        Args:
            date_str: Date as string 'YYYY-MM-DD' or date/datetime object.

        Returns:
            Validated string in 'YYYY-MM-DD' format.

        Raises:
            ValueError for invalid formats.
        """
        if isinstance(date_str, (date, datetime)):
            return date_str.strftime("%Y-%m-%d")
        try:
            datetime.strptime(date_str, "%Y-%m-%d")
            return date_str
        except ValueError:
            raise ValueError(f"Invalid date format: '{date_str}'. Expected 'YYYY-MM-DD'.")

    @staticmethod
    def _validate_debt_type(debt_type: str) -> str:
        """
        Validates the debt type against the schema CHECK constraint.

        Valid values: 'a_favor', 'en_contra'

        Args:
            debt_type: The debt type string.

        Returns:
            Validated lowercase string.

        Raises:
            ValueError for invalid types.
        """
        valid = {"a_favor", "en_contra"}
        normalized = debt_type.lower().strip()
        if normalized not in valid:
            raise ValueError(
                f"Invalid debt type '{debt_type}'. Must be one of: {', '.join(sorted(valid))}."
            )
        return normalized

    def _assert_active(self, debt: sqlite3.Row):
        """
        Ensures a debt is still active before allowing modifications.
        Raises DebtAlreadySettledError if estado is 'saldada' or 'incobrable'.

        Args:
            debt: sqlite3.Row from the deudas table.
        """
        if debt["estado"] != "activa":
            raise DebtAlreadySettledError(
                f"Debt id={debt['id']} is already '{debt['estado']}' and cannot be modified."
            )

    # ----------------------------------------------------------
    # CREATE
    # ----------------------------------------------------------

    def create(
        self,
        person:           str,
        debt_type:        str,
        amount:           float,
        currency_code:    str,
        date_str:         str,
        concept:          str,
        due_date:         Optional[str] = None,
        origen_tipo:      str = "manual",
        origen_id:        Optional[int] = None,
        notes:            Optional[str] = None,
    ) -> DebtResult:
        """
        Creates a new debt record.

        Args:
            person:        Name of the person involved. E.g. 'Noe', 'Papi'.
            debt_type:     'a_favor' (they owe you) or 'en_contra' (you owe them).
            amount:        Positive float amount.
            currency_code: Currency code. E.g. 'ARS', 'USD'.
            date_str:      Start date in 'YYYY-MM-DD' format.
            concept:       Short description. E.g. 'Shared dinner', 'Borrowed cash'.
            due_date:      Optional due date in 'YYYY-MM-DD'. None = no deadline.
            origen_tipo:   Source of the debt: 'manual', 'transaccion', 'compra_cuotas'.
                           Used by OrchestrationService to link the debt to its origin.
            origen_id:     ID of the origin record (transaction_id or compra_id).
                           None for manually created debts.
            notes:         Optional longer description.

        Returns:
            DebtResult with success=True and the new debt's id.

        Raises:
            ValueError for invalid inputs.
        """
        if amount <= 0:
            raise ValueError(f"Amount must be positive. Received: {amount}.")
        if not person or not person.strip():
            raise DebtError("Person name cannot be empty.")
        if not concept or not concept.strip():
            raise DebtError("Concept cannot be empty.")

        validated_date = self._validate_date(date_str)
        validated_type = self._validate_debt_type(debt_type)
        currency       = self._get_currency(currency_code)
        minor          = to_minor(amount, currency["decimales"])

        validated_due = None
        if due_date:
            validated_due = self._validate_date(due_date)

        # Migrado a DeudasRepository (Fase 2, paso 2): concept ahora se
        # persiste en su propia columna deudas.concepto (agregada vía
        # db/schema_migrations.py) — antes se validaba pero nunca se
        # guardaba en ningún lado. notes sigue yendo a deudas.notas, sin
        # cambios.
        debt_id = self._repo.crear(
            person.strip(), validated_type, minor, currency["id"], validated_date,
            validated_due, origen_tipo, origen_id, notes, concepto=concept.strip(),
        )

        return DebtResult(
            success=True,
            debt_id=debt_id,
            data={
                "id":            debt_id,
                "person":        person.strip(),
                "type":          validated_type,
                "amount":        amount,
                "amount_minor":  minor,
                "currency":      currency_code.upper(),
                "date":          validated_date,
                "concept":       concept.strip(),
                "due_date":      validated_due,
                "origen_tipo":   origen_tipo,
                "origen_id":     origen_id,
            },
            message=f"Debt #{debt_id} ({validated_type}) created for '{person.strip()}'.",
        )

    # ----------------------------------------------------------
    # READ
    # ----------------------------------------------------------

    # Migrado a DeudasRepository (Fase 2, paso 2): usa obtener_enriquecida(),
    # que reproduce exactamente esta misma query con JOIN a monedas.
    def get(self, debt_id: int) -> Optional[sqlite3.Row]:
        """
        Fetches a single debt by ID, enriched with currency code via JOIN.

        Args:
            debt_id: Primary key in the deudas table.

        Returns:
            sqlite3.Row if found, None if not.
        """
        return self._repo.obtener_enriquecida(debt_id)

    # Migrado a DeudasRepository (Fase 2, paso 2): usa listar_enriquecida(),
    # que reproduce esta misma query (filtros, orden, paginación y JOIN a
    # monedas).
    def list_debts(
        self,
        person:       Optional[str] = None,
        debt_type:    Optional[str] = None,
        estado:       Optional[str] = None,
        currency_code: Optional[str] = None,
        origen_tipo:  Optional[str] = None,
        page:         int = 1,
        per_page:     int = 50,
    ) -> list[sqlite3.Row]:
        """
        Returns a paginated list of debts with optional filters.
        All filters are optional and AND-combined.

        Args:
            person:        Filter by person name (exact match). None = all.
            debt_type:     Filter by 'a_favor' or 'en_contra'. None = all.
            estado:        Filter by 'activa', 'saldada', 'incobrable'. None = all.
            currency_code: Filter by currency code. None = all.
            origen_tipo:   Filter by origin type. None = all.
            page:          Page number, 1-indexed. Default 1.
            per_page:      Rows per page. Default 50.

        Returns:
            List of sqlite3.Row ordered by fecha_inicio DESC.
        """
        currency_id = None
        if currency_code:
            currency_id = self._get_currency(currency_code)["id"]

        return self._repo.listar_enriquecida(
            entidad_persona=person,
            tipo=debt_type,
            estado=estado,
            moneda_id=currency_id,
            origen_tipo=origen_tipo,
            pagina=page,
            por_pagina=per_page,
        )

    def list_active(
        self,
        debt_type:    Optional[str] = None,
        currency_code: Optional[str] = None,
    ) -> list[sqlite3.Row]:
        """
        Shortcut to list only active debts. Equivalent to list(estado='activa').
        This is the most common query — what you currently owe or are owed.

        Args:
            debt_type:     Optionally filter by 'a_favor' or 'en_contra'.
            currency_code: Optionally filter by currency.

        Returns:
            List of active debt rows.
        """
        return self.list_debts(debt_type=debt_type, estado="activa", currency_code=currency_code)

    # NO migrado a DeudasRepository (Fase 2, paso 2): hace LEFT JOIN con
    # `transacciones` (no `monedas`), una tabla distinta de la que
    # DeudasRepository administra — no es un CRUD de deudas/deuda_pagos, es
    # una consulta que cruza dos dominios. Ver docstring de
    # deudas_repository.py ("Fuera de alcance a propósito").
    def get_payments(self, debt_id: int) -> list[sqlite3.Row]:
        """
        Returns all payment records for a given debt, ordered by date.

        Args:
            debt_id: Primary key in the deudas table.

        Returns:
            List of sqlite3.Row from deuda_pagos, ordered by fecha ASC.
        """
        self._get_debt(debt_id)  # validate existence
        return self._db.fetchall(
            """
            SELECT dp.*, t.fecha AS transaction_date
            FROM deuda_pagos dp
            LEFT JOIN transacciones t ON t.id = dp.transaccion_id
            WHERE dp.deuda_id = ?
            ORDER BY dp.fecha ASC;
            """,
            (debt_id,),
        )

    # NO migrado a DeudasRepository (Fase 2, paso 2): mismo criterio que
    # monthly_summary() en TransactionService — es un reporte agregado
    # (GROUP BY persona+tipo), no un listado de filas de una sola tabla/
    # agregado. Se deja en el service.
    def summary_by_person(self, currency_code: str = "ARS") -> list[sqlite3.Row]:
        """
        Returns a consolidated summary of active debts grouped by person and type.
        Useful for the dashboard: who owes you what, and what you owe to whom.

        Args:
            currency_code: Filter to a single currency for clean display.

        Returns:
            List of rows with: person, type, total_pending_minor, currency_code.
        """
        currency_id = self._get_currency(currency_code)["id"]
        return self._db.fetchall(
            """
            SELECT
                d.entidad_persona       AS person,
                d.tipo,
                m.codigo                AS currency_code,
                SUM(d.monto_pendiente_minor) AS total_pending_minor,
                COUNT(*)                AS debt_count
            FROM deudas d
            JOIN monedas m ON m.id = d.moneda_id
            WHERE d.estado   = 'activa'
              AND d.moneda_id = ?
            GROUP BY d.entidad_persona, d.tipo, d.moneda_id
            ORDER BY d.entidad_persona;
            """,
            (currency_id,),
        )

    def count_debts(
        self,
        person:       Optional[str] = None,
        debt_type:    Optional[str] = None,
        estado:       Optional[str] = None,
        currency_code: Optional[str] = None,
    ) -> int:
        """
        Returns the total count of debts matching the given filters.
        Use together with list() for pagination controls.

        Args:
            Same optional filters as list(), without page/per_page.

        Returns:
            Integer count of matching debts.
        """
        currency_id = None
        if currency_code:
            currency_id = self._get_currency(currency_code)["id"]

        return (
            QueryBuilder("deudas d", include_deleted=True)
            .where("d.entidad_persona", person)
            .where("d.tipo",            debt_type)
            .where("d.estado",          estado)
            .where("d.moneda_id",       currency_id)
            .contar(self._db.conn)
        )

    # ----------------------------------------------------------
    # REGISTER PAYMENT
    # ----------------------------------------------------------

    def register_payment(
        self,
        debt_id:        int,
        amount:         float,
        currency_code:  str,
        date_str:       str,
        payment_type:   str = "transaccion",
        transaction_id: Optional[int] = None,
        concept: Optional[str] = None,
        notes:          Optional[str] = None,
    ) -> DebtResult:
        """
        Registers a (partial or full) payment against a debt.
        Automatically updates monto_pendiente_minor and transitions
        estado to 'saldada' if the balance reaches zero.

        Args:
            debt_id:        The debt being paid.
            amount:         Positive float amount being paid.
            currency_code:  Currency of the payment (must match debt's currency).
            date_str:       Payment date in 'YYYY-MM-DD'.
            payment_type:   'transaccion' (linked to a real transaction),
                            'compensacion' (settled against another debt),
                            'ajuste' (manual correction).
            transaction_id: Optional ID from transacciones if cash actually moved.
                            Provided by OrchestrationService when relevant.
            concept:
            notes:          Optional memo.

        Returns:
            DebtResult with the updated pending balance in data.

        Raises:
            DebtNotFoundError if the debt does not exist.
            DebtAlreadySettledError if the debt is already settled.
            PaymentExceedsBalanceError if payment > pending balance.
            ValueError for invalid inputs.
        """
        if amount <= 0:
            raise ValueError(f"Payment amount must be positive. Received: {amount}.")

        valid_types = {"transaccion", "compensacion", "ajuste"}
        if payment_type not in valid_types:
            raise ValueError(
                f"Invalid payment_type '{payment_type}'. "
                f"Must be one of: {', '.join(sorted(valid_types))}."
            )

        debt         = self._get_debt(debt_id)
        self._assert_active(debt)

        currency     = self._get_currency(currency_code)
        validated_dt = self._validate_date(date_str)
        payment_minor = to_minor(amount, currency["decimales"])

        if payment_minor > debt["monto_pendiente_minor"]:
            pending = from_minor(debt["monto_pendiente_minor"], currency["decimales"])
            raise PaymentExceedsBalanceError(
                f"Payment of {amount} {currency_code.upper()} exceeds "
                f"the pending balance of {pending} {currency_code.upper()}."
            )

        # Migrado a DeudasRepository (Fase 2, paso 2): registrar_pago() ya
        # garantiza la atomicidad INSERT+UPDATE por su cuenta (abre su
        # propia transacción cuando no se le pasa conn) — no hace falta que
        # este método maneje conn/commit/rollback a mano como antes.
        # register_payment() no orquesta ninguna otra escritura además de
        # esta, así que no hay una transacción externa que abrir acá.
        new_pending = debt["monto_pendiente_minor"] - payment_minor
        new_estado  = "saldada" if new_pending == 0 else "activa"

        self._repo.registrar_pago(
            deuda_id=debt_id,
            monto_applied_minor=payment_minor,
            tipo_pago=payment_type,
            fecha=validated_dt,
            nuevo_monto_pendiente_minor=new_pending,
            nuevo_estado=new_estado,
            transaccion_id=transaction_id,
            concepto=concept,
            notas=notes,
        )

        settled = new_estado == "saldada"
        return DebtResult(
            success=True,
            debt_id=debt_id,
            data={
                "payment_minor":   payment_minor,
                "pending_minor":   new_pending,
                "pending_amount":  from_minor(new_pending, currency["decimales"]),
                "currency":        currency_code.upper(),
                "settled":         settled,
                "transaction_id":  transaction_id,
            },
            message=(
                f"Payment of {amount} {currency_code.upper()} registered on debt #{debt_id}. "
                + ("Debt fully settled." if settled else
                   f"Remaining: {from_minor(new_pending, currency['decimales'])} {currency_code.upper()}.")
            ),
        )

    # ----------------------------------------------------------
    # UPDATE
    # ----------------------------------------------------------

    def update(
        self,
        debt_id:   int,
        person:    Optional[str] = None,
        concept:   Optional[str] = None,
        due_date:  Optional[str] = None,
        notes:     Optional[str] = None,
    ) -> DebtResult:
        """
        Updates editable fields of an active debt.
        The amount and currency cannot be changed after creation —
        register a corrective payment (tipo 'ajuste') instead.
        Only active debts can be updated.

        Args:
            debt_id:  The debt to update.
            person:   New person name. None = no change.
            concept:  New concept/description. None = no change.
            due_date: New due date in 'YYYY-MM-DD'. Pass '' to clear. None = no change.
            notes:    New notes. Pass '' to clear. None = no change.

        Returns:
            DebtResult with success=True if at least one field changed.

        Raises:
            DebtNotFoundError if the debt does not exist.
            DebtAlreadySettledError if the debt is already settled.
        """
        debt = self._get_debt(debt_id)
        self._assert_active(debt)

        # Migrado a DeudasRepository (Fase 2, paso 2) — y fix del bug donde
        # concept y notes pisaban la misma columna `notas`: ahora concept va
        # a campos_repo["concepto"] (columna deudas.concepto, agregada vía
        # db/schema_migrations.py) y notes sigue yendo a
        # campos_repo["notas"]. Pasar los dos en la misma llamada ya no hace
        # que uno gane sobre el otro.
        campos_repo: dict = {}

        if person is not None:
            if not person.strip():
                raise DebtError("Person name cannot be empty.")
            campos_repo["entidad_persona"] = person.strip()

        if concept is not None:
            if not concept.strip():
                raise DebtError("Concept cannot be empty.")
            campos_repo["concepto"] = concept.strip()

        if due_date is not None:
            campos_repo["fecha_vencimiento"] = self._validate_date(due_date) if due_date else None

        if notes is not None:
            campos_repo["notas"] = notes or None

        if not campos_repo:
            return DebtResult(
                success=False,
                debt_id=debt_id,
                message="No fields to update were provided.",
            )

        self._repo.actualizar(debt_id, **campos_repo)

        return DebtResult(
            success=True,
            debt_id=debt_id,
            message=f"Debt #{debt_id} updated ({len(campos_repo)} field(s) changed).",
        )

    # ----------------------------------------------------------
    # WRITE OFF
    # ----------------------------------------------------------

    def write_off(self, debt_id: int, notes: Optional[str] = None) -> DebtResult:
        """
        Marks a debt as 'incobrable' (uncollectable / written off).
        Use when you've given up collecting a debt or decided to forgive it.
        Only active debts can be written off.

        Args:
            debt_id: The debt to write off.
            notes:   Optional reason for the write-off.

        Returns:
            DebtResult with success=True.

        Raises:
            DebtNotFoundError if the debt does not exist.
            DebtAlreadySettledError if already settled or written off.
        """
        debt = self._get_debt(debt_id)
        self._assert_active(debt)

        # Migrado a DeudasRepository (Fase 2, paso 2).
        self._repo.write_off(debt_id, notes)

        return DebtResult(
            success=True,
            debt_id=debt_id,
            message=f"Debt #{debt_id} written off.",
        )

