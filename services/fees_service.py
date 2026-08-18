"""
DeltaBalance — services/fees_service.py

Purpose:
    Manages all credit installment operations: purchases made in N installments
    (compras en cuotas), their automatically generated fee schedule (cuotas_credito),
    and the monthly credit card statement (resumenes_tarjeta) that groups fees
    into a single payable amount.

    Owns these tables exclusively:
        - compras_cuotas
        - cuotas_credito
        - resumenes_tarjeta

    Does NOT create transactions. The actual cash outflow (paying the credit card
    statement) is handled by TransactionService. FeesService only manages the
    credit-side tracking.

    Key workflows:
        1. create_purchase()      → creates compras_cuotas + auto-generates N cuotas_credito
        2. open_statement()       → creates or fetches a resumenes_tarjeta for a month
        3. confirm_fee()          → marks a cuota as 'en_resumen', linking it to a statement
        4. close_statement()      → marks 'cerrado' AND consolidates real totals
                                    (monto_consumos_minor, monto_impuestos_minor via
                                    resumen_cargos_extra, and derived porcentaje_impuesto_bp)
        5. pay_statement()        → marks it 'pagado', writes the paid amount
                                    (monto_pagado_minor, now required); caller
                                    creates the transaction separately

    Fee projection:
        fees_by_month()           → returns pending fees grouped by month/account/currency
                                    (equivalent to your original Google Sheets monthly table)
"""

import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional

from db.database import DatabaseManager, to_minor, from_minor
from db.query_builder import QueryBuilder
from utils.money import amount_display
from repositories.compras_cuotas_repository import ComprasCuotasRepository
from repositories.cuotas_credito_repository import CuotasCreditoRepository
from repositories.resumenes_tarjeta_repository import ResumenesTarjetaRepository
from repositories.resumen_cargos_extra_repository import ResumenCargosExtraRepository

# =============================================================
# EXCEPTIONS
# =============================================================

class FeesError(Exception):
    """Raised when a fees operation violates a business rule."""


class PurchaseNotFoundError(FeesError):
    """Raised when a referenced purchase does not exist."""


class StatementNotFoundError(FeesError):
    """Raised when a referenced statement does not exist."""


class FeeNotFoundError(FeesError):
    """Raised when a referenced fee does not exist."""


class StatementAlreadyPaidError(FeesError):
    """Raised when attempting to modify an already paid statement."""


class StatementAlreadyClosedError(FeesError):
    """Raised when attempting to modify a closed (but not yet paid) statement."""


class FeeAlreadyConfirmedError(FeesError):
    """Raised when a fee is already linked to a statement."""


# =============================================================
# RESULT TYPE
# =============================================================

@dataclass
class FeesResult:
    """
    Structured result returned by FeesService operations.
    """
    success:      bool
    entity_id:    Optional[int]   = None
    data:         dict            = field(default_factory=dict)
    message:      str             = ""


# =============================================================
# SERVICE
# =============================================================

class FeesService:
    """
    Entry point for all installment and credit card statement operations.

    Usage:
        db  = DatabaseManager()
        svc = FeesService(db)

        # Register a purchase in 12 installments
        result = svc.create_purchase(
            date_str="2026-05-01",
            concept="Washing machine",
            account_id=2,           # the credit card account
            category_id=8,
            currency_code="ARS",
            total_amount=360000.0,
            total_fees=12,
        )
        # → automatically generates 12 cuotas_credito rows, one per month
    """

    def __init__(self, db: DatabaseManager):
        """
        Initializes the service with an active DatabaseManager instance.

        Args:
            db: An initialized DatabaseManager. Schema + seed must already be applied.
        """
        self._db = db
        self._compras_repo = ComprasCuotasRepository(db)
        self._cuotas_repo = CuotasCreditoRepository(db)
        self._resumenes_repo = ResumenesTarjetaRepository(db)
        self._cargos_repo = ResumenCargosExtraRepository(db)

    # ----------------------------------------------------------
    # INTERNAL HELPERS
    # ----------------------------------------------------------

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

    def _get_account(self, account_id: int) -> sqlite3.Row:
        """
        Fetches an active account by ID. Raises FeesError if not found or inactive.

        Args:
            account_id: Primary key of the cuentas table.

        Returns:
            sqlite3.Row for the account.
        """
        row = self._db.fetchone(
            "SELECT * FROM cuentas WHERE id = ? AND activa = 1;", (account_id,)
        )
        if row is None:
            raise FeesError(f"Account id={account_id} not found or is inactive.")
        return row

    def _get_category(self, category_id: int) -> sqlite3.Row:
        """
        Fetches a category by ID. Raises FeesError if not found.

        Args:
            category_id: Primary key of the categorias table.

        Returns:
            sqlite3.Row for the category.
        """
        row = self._db.fetchone("SELECT * FROM categorias WHERE id = ?;", (category_id,))
        if row is None:
            raise FeesError(f"Category id={category_id} not found.")
        return row

    def _get_purchase(self, purchase_id: int) -> sqlite3.Row:
        """
        Fetches a purchase row by ID. Raises PurchaseNotFoundError if not found.

        Args:
            purchase_id: Primary key of the compras_cuotas table.

        Returns:
            sqlite3.Row for the purchase.
        """
        row = self._compras_repo.obtener_por_id(purchase_id)
        if row is None:
            raise PurchaseNotFoundError(f"Purchase id={purchase_id} not found.")
        return row

    def _get_statement(self, statement_id: int) -> sqlite3.Row:
        """
        Fetches a credit card statement by ID.
        Raises StatementNotFoundError if not found.

        Args:
            statement_id: Primary key of the resumenes_tarjeta table.

        Returns:
            sqlite3.Row for the statement.
        """
        row = self._resumenes_repo.obtener_por_id(statement_id)
        if row is None:
            raise StatementNotFoundError(f"Statement id={statement_id} not found.")
        return row

    @staticmethod
    def _validate_date(date_str) -> str:
        """
        Validates and normalizes a date to 'YYYY-MM-DD'.
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
            raise ValueError(
                f"Invalid date format: '{date_str}'. Expected 'YYYY-MM-DD'."
            )

    @staticmethod
    def _advance_month(month: int, year: int) -> tuple[int, int]:
        """
        Advances a month/year pair by one month, wrapping December → January.

        Args:
            month: Current month (1–12).
            year:  Current year.

        Returns:
            Tuple (next_month, next_year).
        """
        month += 1
        if month > 12:
            month = 1
            year += 1
        return month, year

    # ----------------------------------------------------------
    # CREATE PURCHASE
    # ----------------------------------------------------------

    def create_purchase(
        self,
        date_str:         str,
        concept:          str,
        account_id:       int,
        category_id:      int,
        currency_code:    str,
        total_amount:     float,
        total_fees:       int,
        amount_per_fee:   Optional[float] = None,
        notes:            Optional[str]   = None,
    ) -> FeesResult:
        """
        Creates a purchase in installments and auto-generates all N fee rows.
        The fees are projected month by month starting from the purchase month.

        If amount_per_fee is not provided, it is calculated as total_amount / total_fees.
        Pass a custom amount_per_fee when the bank applies interest and the per-fee
        amount differs from a simple division (e.g. 12 fees with CFT).

        Args:
            date_str:       Purchase date in 'YYYY-MM-DD'. First fee falls on this month.
            concept:        Description. E.g. 'Washing machine', 'iPhone 15'.
            account_id:     The credit card account used for the purchase.
            category_id:    Category for classification.
            currency_code:  Currency of the purchase.
            total_amount:   Full purchase price as a positive float.
            total_fees:     Number of installments (>= 1).
            amount_per_fee: Optional per-fee amount. If None, computed automatically.
            notes:          Optional description.

        Returns:
            FeesResult with the purchase id and a summary of generated fees.

        Raises:
            FeesError for invalid inputs.
            ValueError for invalid date or currency.
        """
        if total_amount <= 0:
            raise FeesError(f"Total amount must be positive. Received: {total_amount}.")
        if total_fees < 1:
            raise FeesError(f"Total fees must be >= 1. Received: {total_fees}.")
        if not concept or not concept.strip():
            raise FeesError("Concept cannot be empty.")

        validated_date = self._validate_date(date_str)
        currency       = self._get_currency(currency_code)
        dec            = currency["decimales"]

        self._get_account(account_id)    # validate existence
        self._get_category(category_id)  # validate existence

        total_minor     = to_minor(total_amount, dec)
        per_fee_amount  = amount_per_fee if amount_per_fee is not None else total_amount / total_fees
        per_fee_minor   = to_minor(per_fee_amount, dec)

        conn = self._db.conn
        with self._db.transaction():
            # Create the purchase record
            purchase_id = self._compras_repo.crear(
                fecha_compra=validated_date,
                concepto=concept.strip(),
                cuenta_id=account_id,
                categoria_id=category_id,
                moneda_id=currency["id"],
                monto_total_minor=total_minor,
                total_cuotas=total_fees,
                monto_por_cuota_minor=per_fee_minor,
                notas=notes,
                conn=conn,
            )

            # Auto-generate N fee rows projected month by month
            dt    = datetime.strptime(validated_date, "%Y-%m-%d")
            month = dt.month
            year  = dt.year

            cuotas = []
            for n in range(1, total_fees + 1):
                cuotas.append({
                    "numero_cuota": n,
                    "mes_proyectado": month,
                    "anio_proyectado": year,
                    "monto_cuota_minor": per_fee_minor,
                })
                month, year = self._advance_month(month, year)

            self._cuotas_repo.crear_lote(compra_id=purchase_id, cuotas=cuotas, conn=conn)

        return FeesResult(
            success=True,
            entity_id=purchase_id,
            data={
                "purchase_id":    purchase_id,
                "concept":        concept.strip(),
                "total_amount":   total_amount,
                "total_fees":     total_fees,
                "amount_per_fee": per_fee_amount,
                "currency":       currency_code.upper(),
                "account_id":     account_id,
            },
            message=(
                f"Purchase '{concept.strip()}' created with {total_fees} fee(s) "
                f"of {per_fee_amount:.2f} {currency_code.upper()} each."
            ),
        )

    # ----------------------------------------------------------
    # READ PURCHASES
    # ----------------------------------------------------------

    def get_purchase(self, purchase_id: int) -> Optional[sqlite3.Row]:
        """
        Fetches a single purchase enriched with account, category, and currency info.

        Args:
            purchase_id: Primary key in compras_cuotas.

        Returns:
            sqlite3.Row if found, None if not.
        """
        return self._compras_repo.obtener_enriquecida(purchase_id)

    def list_purchases(
        self,
        account_id:    Optional[int] = None,
        estado:        Optional[str] = None,
        currency_code: Optional[str] = None,
        page:          int = 1,
        per_page:      int = 50,
    ) -> list[sqlite3.Row]:
        """
        Returns a paginated list of purchases with optional filters.

        Args:
            account_id:    Filter by credit card account. None = all.
            estado:        Filter by 'activa', 'completada', 'cancelada'. None = all.
            currency_code: Filter by currency. None = all.
            page:          Page number, 1-indexed.
            per_page:      Rows per page.

        Returns:
            List of sqlite3.Row ordered by fecha_compra DESC.
        """
        currency_id = None
        if currency_code:
            currency_id = self._get_currency(currency_code)["id"]

        return self._compras_repo.listar_enriquecida(
            cuenta_id=account_id,
            estado=estado,
            moneda_id=currency_id,
            pagina=page,
            por_pagina=per_page,
        )

    def get_fees_for_purchase(self, purchase_id: int) -> list[sqlite3.Row]:
        """
        Returns all fee rows for a given purchase, ordered by fee number.

        Args:
            purchase_id: Primary key in compras_cuotas.

        Returns:
            List of cuotas_credito rows ordered by numero_cuota ASC.
        """
        self._get_purchase(purchase_id)  # validate existence
        return self._cuotas_repo.listar_por_compra(purchase_id)

    # ----------------------------------------------------------
    # FEE PROJECTION (the Google Sheets equivalent)
    # ----------------------------------------------------------

    # NO migrado (Fase 2, COMPRAS_CUOTAS paso 2): es un reporte agregado
    # (GROUP BY cuenta+moneda, con COUNT/SUM y JOINs a compras_cuotas/
    # cuentas/monedas) — mismo criterio que monthly_summary()/
    # summary_by_person() en los bloques anteriores. NO es lo mismo que
    # CuotasCreditoRepository.listar_por_mes(), que devuelve filas planas de
    # cuotas_credito sin agregación ni JOINs; ese método no cubre lo que
    # fees_by_month() necesita.
    def fees_by_month(
        self,
        month:         int,
        year:          int,
        account_id:    Optional[int] = None,
        currency_code: Optional[str] = None,
        estado:        str = "pendiente",
    ) -> list[sqlite3.Row]:
        """
        Returns pending fees for a given month grouped by account and currency.
        This is the direct equivalent of your Google Sheets monthly credit table:
        one row per (bank, currency) with the total amount due.

        Args:
            month:         Target month (1–12).
            year:          Target year.
            account_id:    Optionally filter to a single credit card account.
            currency_code: Optionally filter to a single currency.
            estado:        Fee status to include. Default 'pendiente'.
                           Use 'en_resumen' to see fees already in a statement.

        Returns:
            List of rows with: account_name, currency_code, fee_count, total_minor.
        """
        if not (1 <= month <= 12):
            raise ValueError(f"Month must be between 1 and 12. Received: {month}.")

        currency_id = None
        if currency_code:
            currency_id = self._get_currency(currency_code)["id"]

        return (
            QueryBuilder("cuotas_credito qc", include_deleted=True)
            .select(
                "c.id AS account_id",
                "c.nombre AS account_name",
                "m.codigo AS currency_code",
                "m.simbolo AS currency_symbol",
                "m.decimales",
                "COUNT(*) AS fee_count",
                "SUM(qc.monto_cuota_minor) AS total_minor",
            )
            .join("compras_cuotas pc", "pc.id = qc.compra_id")
            .join("cuentas c",         "c.id = pc.cuenta_id")
            .join("monedas m",         "m.id = pc.moneda_id")
            .where("qc.mes_proyectado",  month)
            .where("qc.anio_proyectado", year)
            .where("qc.estado",          estado)
            .where("pc.cuenta_id",       account_id)
            .where("pc.moneda_id",       currency_id)
            .group_by("c.id", "m.id")
            .order("c.nombre")
            .ejecutar(self._db.conn)
        )

    # NO migrado: no accede a ninguna tabla directamente — solo orquesta N
    # llamadas a fees_by_month() (que tampoco migró, ver nota arriba). No
    # hay SQL propio acá para mover a un repositorio.
    def fees_projection(
        self,
        months:        int = 6,
        currency_code: str = "ARS",
    ) -> list[dict]:
        """
        Projects total fees due for the next N months across all accounts.
        Useful for the dashboard projection panel.

        Args:
            months:        How many months ahead to project. Default 6.
            currency_code: Currency to project. Default 'ARS'.

        Returns:
            List of dicts: [{"month": 5, "year": 2026, "total_minor": 123456}, ...]
        """
        today = datetime.today()
        month, year = today.month, today.year
        result = []

        for _ in range(months):
            rows  = self.fees_by_month(month, year, currency_code=currency_code)
            total = sum(r["total_minor"] for r in rows)
            result.append({"month": month, "year": year, "total_minor": total})
            month, year = self._advance_month(month, year)

        return result

    # ----------------------------------------------------------
    # STATEMENT MANAGEMENT
    # ----------------------------------------------------------

    def open_statement(
        self,
        account_id:     int,
        month:          int,
        year:           int,
        tax_percentage_bp: int = 0,
    ) -> FeesResult:
        """
        Creates a new credit card statement for the given account/month/year,
        or returns the existing one if already created (idempotent).

        The monto_consumos_minor is initialized to 0 and grows as fees are
        confirmed via confirm_fee(). monto_total_pagado_minor is recalculated
        automatically on each confirm_fee() call.

        Args:
            account_id:         The credit card account.
            month:              Statement month (1–12).
            year:               Statement year.
            tax_percentage_bp:  Tax on purchases in basis points.
                                E.g. 3000 = 30% (Bienes Personales / PAIS).

        Returns:
            FeesResult with the statement id.
        """
        if not (1 <= month <= 12):
            raise ValueError(f"Month must be between 1 and 12. Received: {month}.")
        self._get_account(account_id)

        # Idempotent — return existing if already created
        existing = self._resumenes_repo.obtener_por_periodo(account_id, month, year)
        if existing:
            return FeesResult(
                success=True,
                entity_id=existing["id"],
                data={"statement_id": existing["id"], "already_existed": True},
                message=f"Statement for {month:02d}/{year} already exists (id={existing['id']}).",
            )

        stmt_id = self._resumenes_repo.crear(
            cuenta_id=account_id,
            mes=month,
            anio=year,
            porcentaje_impuesto_bp=tax_percentage_bp,
        )

        return FeesResult(
            success=True,
            entity_id=stmt_id,
            data={
                "statement_id":      stmt_id,
                "account_id":        account_id,
                "month":             month,
                "year":              year,
                "tax_percentage_bp": tax_percentage_bp,
                "already_existed":   False,
            },
            message=f"Statement created for {month:02d}/{year} (id={stmt_id}).",
        )

    def confirm_fee(
        self,
        fee_id:       int,
        statement_id: int,
        real_month:   Optional[int] = None,
        real_year:    Optional[int] = None,
        notes:        Optional[str] = None,
    ) -> FeesResult:
        """
        Confirms that a fee appeared in a specific credit card statement.
        Marks the fee as 'en_resumen' and links it to the statement.
        Updates the statement's monto_consumos_minor automatically.

        real_month / real_year allow recording when a fee was skipped by the bank
        and appeared a month later than projected (your 'omitida/duplicada' tracking).

        Args:
            fee_id:       The cuotas_credito row to confirm.
            statement_id: The resumenes_tarjeta to link it to.
            real_month:   Actual month it appeared. Defaults to projected month.
            real_year:    Actual year it appeared. Defaults to projected year.
            notes:        Optional note. E.g. 'Omitted in May, appeared in June'.

        Returns:
            FeesResult with updated statement totals.

        Raises:
            FeeNotFoundError if the fee does not exist.
            FeeAlreadyConfirmedError if already linked to a statement.
            StatementNotFoundError if the statement does not exist.
            StatementAlreadyPaidError if the statement is already paid.
        """
        fee = self._cuotas_repo.obtener_por_id(fee_id)
        if fee is None:
            raise FeeNotFoundError(f"Fee id={fee_id} not found.")

        if fee["estado"] in ("en_resumen", "pagado"):
            raise FeeAlreadyConfirmedError(
                f"Fee id={fee_id} is already '{fee['estado']}'."
            )

        statement = self._get_statement(statement_id)
        if statement["estado"] == "pagado":
            raise StatementAlreadyPaidError(
                f"Statement id={statement_id} is already paid and cannot be modified."
            )

        actual_month = real_month or fee["mes_proyectado"]
        actual_year  = real_year  or fee["anio_proyectado"]

        # Same arithmetic as before (SQLite's integer "/" truncates like
        # Python's "//" for positive operands): monto_total_pagado_minor
        # keeps being an incremental estimate updated on every confirm_fee()
        # call, using whatever porcentaje_impuesto_bp the statement already
        # has (still the manual value from open_statement() — the DERIVED
        # bp only replaces it at close_statement() time, see
        # ResumenesTarjetaRepository.marcar_cerrado()). Preserved exactly,
        # not one of the two sanctioned behavior changes for this task.
        new_consumos = statement["monto_consumos_minor"] + fee["monto_cuota_minor"]
        new_total = (new_consumos * (10000 + statement["porcentaje_impuesto_bp"])) // 10000

        conn = self._db.conn
        with self._db.transaction():
            # Link fee to statement
            self._cuotas_repo.marcar_estado(
                fee_id, "en_resumen",
                resumen_id=statement_id,
                mes_real_pago=actual_month,
                anio_real_pago=actual_year,
                notas=notes,
                conn=conn,
            )

            # Update statement consumos total
            self._resumenes_repo.actualizar_totales(
                statement_id,
                monto_consumos_minor=new_consumos,
                monto_total_pagado_minor=new_total,
                conn=conn,
            )

        updated_stmt = self._get_statement(statement_id)
        return FeesResult(
            success=True,
            entity_id=statement_id,
            data={
                "fee_id":            fee_id,
                "statement_id":      statement_id,
                "fee_minor":         fee["monto_cuota_minor"],
                "statement_total":   updated_stmt["monto_total_pagado_minor"],
                "real_month":        actual_month,
                "real_year":         actual_year,
            },
            message=f"Fee #{fee_id} confirmed on statement #{statement_id}.",
        )

    # ----------------------------------------------------------
    # EXTRA CHARGES (resumen_cargos_extra)
    # ----------------------------------------------------------

    _EXTRA_CHARGE_TYPES = ("impuesto", "recargo", "ajuste", "otro")

    def add_extra_charge(
        self,
        statement_id: int,
        concept:      str,
        charge_type:  str,
        amount_minor: int,
    ) -> FeesResult:
        """
        Adds an extra charge (tax, surcharge, adjustment) to an OPEN
        statement. Does NOT touch resumenes_tarjeta.monto_impuestos_minor /
        porcentaje_impuesto_bp — those are only recalculated for real by
        close_statement() (see its docstring and
        docs/DATA_MODEL_DECISIONS.md sección 12). Adding a charge here only
        writes to resumen_cargos_extra.

        Args:
            statement_id: The statement to add the charge to. Must be
                         'abierto' — 'cerrado'/'pagado' statements reject
                         further charges.
            concept:      Description. E.g. 'IVA', 'Impuesto PAIS'.
            charge_type:  One of 'impuesto', 'recargo', 'ajuste', 'otro'.
            amount_minor: The charge amount in minor units. Can be negative
                         (e.g. an adjustment in the user's favor).

        Returns:
            FeesResult with the new charge id.

        Raises:
            StatementNotFoundError if the statement does not exist.
            FeesError if concept is empty.
            ValueError if charge_type is not one of the valid types.
            StatementAlreadyPaidError if the statement is already paid.
            StatementAlreadyClosedError if the statement is closed (but not paid).
        """
        statement = self._get_statement(statement_id)

        if not concept or not concept.strip():
            raise FeesError("Concept cannot be empty.")
        if charge_type not in self._EXTRA_CHARGE_TYPES:
            raise ValueError(
                f"Invalid charge_type: '{charge_type}'. "
                f"Expected one of {self._EXTRA_CHARGE_TYPES}."
            )

        if statement["estado"] == "pagado":
            raise StatementAlreadyPaidError(
                f"Statement id={statement_id} is already paid and cannot be modified."
            )
        if statement["estado"] == "cerrado":
            raise StatementAlreadyClosedError(
                f"Statement id={statement_id} is closed and cannot be modified."
            )

        charge_id = self._cargos_repo.agregar(
            statement_id, concept.strip(), charge_type, amount_minor,
        )

        return FeesResult(
            success=True,
            entity_id=charge_id,
            data={
                "charge_id":    charge_id,
                "statement_id": statement_id,
                "concept":      concept.strip(),
                "charge_type":  charge_type,
                "amount_minor": amount_minor,
            },
            message=f"Extra charge '{concept.strip()}' ({charge_type}) added to statement #{statement_id}.",
        )

    def list_extra_charges(self, statement_id: int) -> list[sqlite3.Row]:
        """
        Returns all extra charges of a statement.

        Args:
            statement_id: Primary key in resumenes_tarjeta.

        Returns:
            List of resumen_cargos_extra rows.

        Raises:
            StatementNotFoundError if the statement does not exist.
        """
        self._get_statement(statement_id)  # validate existence
        return self._cargos_repo.listar_por_resumen(statement_id)

    def remove_extra_charge(self, charge_id: int, statement_id: int) -> FeesResult:
        """
        Removes an extra charge from an OPEN statement. Requires both ids
        so the charge's ownership can be validated — a charge_id that
        exists but belongs to a different statement is rejected, instead
        of silently deleting the wrong row.

        Does NOT touch resumenes_tarjeta.monto_impuestos_minor /
        porcentaje_impuesto_bp — same reasoning as add_extra_charge().

        Args:
            charge_id:    The resumen_cargos_extra row to remove.
            statement_id: The statement it must belong to.

        Returns:
            FeesResult with success=True.

        Raises:
            StatementNotFoundError if the statement does not exist.
            FeesError if the charge does not exist or belongs to a
                      different statement.
            StatementAlreadyPaidError if the statement is already paid.
            StatementAlreadyClosedError if the statement is closed (but not paid).
        """
        statement = self._get_statement(statement_id)

        charge = self._cargos_repo.obtener_por_id(charge_id)
        if charge is None or charge["resumen_id"] != statement_id:
            raise FeesError(
                f"Extra charge id={charge_id} not found on statement id={statement_id}."
            )

        if statement["estado"] == "pagado":
            raise StatementAlreadyPaidError(
                f"Statement id={statement_id} is already paid and cannot be modified."
            )
        if statement["estado"] == "cerrado":
            raise StatementAlreadyClosedError(
                f"Statement id={statement_id} is closed and cannot be modified."
            )

        self._cargos_repo.eliminar(charge_id)

        return FeesResult(
            success=True,
            entity_id=charge_id,
            data={"charge_id": charge_id, "statement_id": statement_id},
            message=f"Extra charge #{charge_id} removed from statement #{statement_id}.",
        )

    def close_statement(self, statement_id: int) -> FeesResult:
        """
        Marks a statement as 'cerrado' (reviewed, ready to pay) and
        CONSOLIDATES its totals for real: monto_consumos_minor is
        recalculated as the sum of every cuotas_credito row linked to this
        statement, monto_impuestos_minor as the sum of every
        resumen_cargos_extra row of this statement, and
        porcentaje_impuesto_bp is derived from both
        (monto_impuestos_minor*10000 // monto_consumos_minor, 0 if there are
        no consumos). This is one of the two intentionally sanctioned
        behavior changes of this migration (see
        docs/DATA_MODEL_DECISIONS.md sección 12) — before this, closing a
        statement only flipped `estado`, it never touched the totals.

        Note: this does NOT touch monto_total_pagado_minor — that field is
        only written by pay_statement() (see docstring there).

        Args:
            statement_id: The statement to close.

        Returns:
            FeesResult with the three consolidated totals in `data`.

        Raises:
            StatementNotFoundError if not found.
            StatementAlreadyPaidError if already paid.
        """
        statement = self._get_statement(statement_id)
        if statement["estado"] == "pagado":
            raise StatementAlreadyPaidError(
                f"Statement id={statement_id} is already paid."
            )

        totales = self._resumenes_repo.marcar_cerrado(statement_id)

        return FeesResult(
            success=True,
            entity_id=statement_id,
            data={
                "monto_consumos_minor":   totales["monto_consumos_minor"],
                "monto_impuestos_minor":  totales["monto_impuestos_minor"],
                "porcentaje_impuesto_bp": totales["porcentaje_impuesto_bp"],
            },
            message=(
                f"Statement #{statement_id} closed. "
                f"Consumos: {totales['monto_consumos_minor']} minor, "
                f"Impuestos: {totales['monto_impuestos_minor']} minor, "
                f"Tax: {totales['porcentaje_impuesto_bp']} bp."
            ),
        )

    def pay_statement(
        self,
        statement_id:       int,
        payment_date:       str,
        monto_pagado_minor: int,
        transaction_id:     Optional[int] = None,
    ) -> FeesResult:
        """
        Marks a statement as 'pagado' and all its linked fees as 'pagado'.
        The actual cash transaction (egreso from your bank account) must be
        created separately via TransactionService and its id passed here.

        CAMBIO DE FIRMA (Fase 2, COMPRAS_CUOTAS paso 2, sanctioned behavior
        change): monto_pagado_minor is now a required parameter, written to
        monto_total_pagado_minor. Before this migration, pay_statement()
        never wrote monto_total_pagado_minor at all — it stayed whatever
        confirm_fee() had last accumulated using the statement's manual
        porcentaje_impuesto_bp (see confirm_fee() docstring). Now the
        caller passes the real amount paid explicitly.

        Args:
            statement_id:       The statement being paid.
            payment_date:       Payment date in 'YYYY-MM-DD'.
            monto_pagado_minor: The real amount paid, in minor units. Written
                                to monto_total_pagado_minor.
            transaction_id:     Optional ID from TransactionService.create()
                                that represents the actual cash outflow.

        Returns:
            FeesResult with success=True.

        Raises:
            StatementNotFoundError if not found.
            StatementAlreadyPaidError if already paid.
        """
        statement    = self._get_statement(statement_id)
        validated_dt = self._validate_date(payment_date)

        if statement["estado"] == "pagado":
            raise StatementAlreadyPaidError(
                f"Statement id={statement_id} is already paid."
            )

        conn = self._db.conn
        with self._db.transaction():
            # Mark all fees in this statement as paid
            self._cuotas_repo.marcar_estado_por_resumen(
                statement_id, "en_resumen", "pagado", conn=conn,
            )

            # Mark the statement itself as paid
            self._resumenes_repo.marcar_pagado(
                statement_id,
                fecha_pago=validated_dt,
                monto_pagado_minor=monto_pagado_minor,
                conn=conn,
            )

        return FeesResult(
            success=True,
            entity_id=statement_id,
            data={
                "statement_id":  statement_id,
                "payment_date":  validated_dt,
                "total_paid":    monto_pagado_minor,
                "transaction_id": transaction_id,
            },
            message=(
                f"Statement #{statement_id} marked as paid on {validated_dt}."
                + (f" Linked to transaction #{transaction_id}." if transaction_id else "")
            ),
        )

    # ----------------------------------------------------------
    # READ STATEMENTS
    # ----------------------------------------------------------

    def get_statement(self, statement_id: int) -> Optional[sqlite3.Row]:
        """
        Fetches a single statement enriched with account name.

        Args:
            statement_id: Primary key in resumenes_tarjeta.

        Returns:
            sqlite3.Row if found, None if not.
        """
        return self._resumenes_repo.obtener_enriquecida(statement_id)

    def list_statements(
        self,
        account_id: Optional[int] = None,
        estado:     Optional[str] = None,
        year:       Optional[int] = None,
        page:       int = 1,
        per_page:   int = 24,
    ) -> list[sqlite3.Row]:
        """
        Returns a paginated list of credit card statements.

        Args:
            account_id: Filter by account. None = all.
            estado:     Filter by 'abierto', 'cerrado', 'pagado'. None = all.
            year:       Filter by year. None = all years.
            page:       Page number, 1-indexed.
            per_page:   Rows per page. Default 24 (2 years of monthly statements).

        Returns:
            List of sqlite3.Row ordered by year DESC, month DESC.
        """
        return self._resumenes_repo.listar_enriquecida(
            cuenta_id=account_id,
            estado=estado,
            anio=year,
            pagina=page,
            por_pagina=per_page,
        )

    # ----------------------------------------------------------
    # CANCEL PURCHASE
    # ----------------------------------------------------------

    def cancel_purchase(self, purchase_id: int, notes: Optional[str] = None) -> FeesResult:
        """
        Cancels a purchase and marks all its pending fees as 'omitido'.
        Only fees still in 'pendiente' state are affected — fees already
        in a statement (en_resumen / pagado) are left untouched.

        Args:
            purchase_id: The purchase to cancel.
            notes:       Optional reason for cancellation.

        Returns:
            FeesResult with the count of fees cancelled.

        Raises:
            PurchaseNotFoundError if not found.
            FeesError if already cancelled or completed.
        """
        purchase = self._get_purchase(purchase_id)
        if purchase["estado"] in ("cancelada", "completada"):
            raise FeesError(
                f"Purchase id={purchase_id} is already '{purchase['estado']}'."
            )

        conn = self._db.conn
        with self._db.transaction():
            fees_cancelled = self._cuotas_repo.marcar_estado_por_compra(
                purchase_id, "pendiente", "omitido", notas=notes, conn=conn,
            )
            self._compras_repo.cancelar(purchase_id, notas=notes, conn=conn)

        return FeesResult(
            success=True,
            entity_id=purchase_id,
            data={"fees_cancelled": fees_cancelled},
            message=(
                f"Purchase #{purchase_id} cancelled. "
                f"{fees_cancelled} pending fee(s) marked as omitido."
            ),
        )

