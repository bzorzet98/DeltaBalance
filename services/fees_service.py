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
        4. close_statement()      → marks the statement as 'cerrado' for review
        5. pay_statement()        → marks it 'pagado'; caller creates the transaction separately

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
        row = self._db.fetchone(
            "SELECT * FROM compras_cuotas WHERE id = ?;", (purchase_id,)
        )
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
        row = self._db.fetchone(
            "SELECT * FROM resumenes_tarjeta WHERE id = ?;", (statement_id,)
        )
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
        try:
            # Create the purchase record
            cur = conn.execute(
                """
                INSERT INTO compras_cuotas
                    (fecha_compra, concepto, cuenta_id, categoria_id, moneda_id,
                     monto_total_minor, total_cuotas, monto_por_cuota_minor, notas)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    validated_date, concept.strip(), account_id, category_id,
                    currency["id"], total_minor, total_fees, per_fee_minor, notes,
                ),
            )
            purchase_id = cur.lastrowid

            # Auto-generate N fee rows projected month by month
            dt    = datetime.strptime(validated_date, "%Y-%m-%d")
            month = dt.month
            year  = dt.year

            for n in range(1, total_fees + 1):
                conn.execute(
                    """
                    INSERT INTO cuotas_credito
                        (compra_id, numero_cuota, mes_proyectado,
                         anio_proyectado, monto_cuota_minor)
                    VALUES (?, ?, ?, ?, ?);
                    """,
                    (purchase_id, n, month, year, per_fee_minor),
                )
                month, year = self._advance_month(month, year)

            conn.commit()

        except Exception:
            conn.rollback()
            raise

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
        return (
            QueryBuilder("compras_cuotas pc", include_deleted=True)
            .select(
                "pc.*",
                "c.nombre AS account_name",
                "cat.subcategoria AS category_name",
                "m.codigo AS currency_code",
                "m.simbolo AS currency_symbol",
                "m.decimales",
            )
            .join("cuentas c",      "c.id = pc.cuenta_id")
            .join("categorias cat", "cat.id = pc.categoria_id")
            .join("monedas m",      "m.id = pc.moneda_id")
            .where("pc.id", purchase_id)
            .ejecutar_uno(self._db.conn)
        )

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

        return (
            QueryBuilder("compras_cuotas pc", include_deleted=True)
            .select(
                "pc.*",
                "c.nombre AS account_name",
                "cat.subcategoria AS category_name",
                "m.codigo AS currency_code",
                "m.decimales",
            )
            .join("cuentas c",      "c.id = pc.cuenta_id")
            .join("categorias cat", "cat.id = pc.categoria_id")
            .join("monedas m",      "m.id = pc.moneda_id")
            .where("pc.cuenta_id", account_id)
            .where("pc.estado",    estado)
            .where("pc.moneda_id", currency_id)
            .order("pc.fecha_compra", "DESC")
            .paginar(page, per_page)
            .ejecutar(self._db.conn)
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
        return self._db.fetchall(
            """
            SELECT * FROM cuotas_credito
            WHERE compra_id = ?
            ORDER BY numero_cuota ASC;
            """,
            (purchase_id,),
        )

    # ----------------------------------------------------------
    # FEE PROJECTION (the Google Sheets equivalent)
    # ----------------------------------------------------------

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
        existing = self._db.fetchone(
            """
            SELECT * FROM resumenes_tarjeta
            WHERE cuenta_id = ? AND mes = ? AND anio = ?;
            """,
            (account_id, month, year),
        )
        if existing:
            return FeesResult(
                success=True,
                entity_id=existing["id"],
                data={"statement_id": existing["id"], "already_existed": True},
                message=f"Statement for {month:02d}/{year} already exists (id={existing['id']}).",
            )

        stmt_id = self._db.execute(
            """
            INSERT INTO resumenes_tarjeta
                (cuenta_id, mes, anio, monto_consumos_minor,
                 monto_impuestos_minor, porcentaje_impuesto_bp, monto_total_pagado_minor)
            VALUES (?, ?, ?, 0, 0, ?, 0);
            """,
            (account_id, month, year, tax_percentage_bp),
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
        fee = self._db.fetchone(
            "SELECT * FROM cuotas_credito WHERE id = ?;", (fee_id,)
        )
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

        conn = self._db.conn
        try:
            # Link fee to statement
            conn.execute(
                """
                UPDATE cuotas_credito
                SET estado = 'en_resumen', resumen_id = ?,
                    mes_real_pago = ?, anio_real_pago = ?, notas = ?
                WHERE id = ?;
                """,
                (statement_id, actual_month, actual_year, notes, fee_id),
            )

            # Update statement consumos total
            conn.execute(
                """
                UPDATE resumenes_tarjeta
                SET monto_consumos_minor = monto_consumos_minor + ?,
                    monto_total_pagado_minor = (
                        (monto_consumos_minor + ?) *
                        (10000 + porcentaje_impuesto_bp) / 10000
                    )
                WHERE id = ?;
                """,
                (fee["monto_cuota_minor"], fee["monto_cuota_minor"], statement_id),
            )
            conn.commit()

        except Exception:
            conn.rollback()
            raise

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

    def close_statement(self, statement_id: int) -> FeesResult:
        """
        Marks a statement as 'cerrado' (reviewed, ready to pay).
        After closing, no more fees can be added to it.

        Args:
            statement_id: The statement to close.

        Returns:
            FeesResult with success=True.

        Raises:
            StatementNotFoundError if not found.
            StatementAlreadyPaidError if already paid.
        """
        statement = self._get_statement(statement_id)
        if statement["estado"] == "pagado":
            raise StatementAlreadyPaidError(
                f"Statement id={statement_id} is already paid."
            )

        self._db.execute(
            "UPDATE resumenes_tarjeta SET estado = 'cerrado' WHERE id = ?;",
            (statement_id,),
        )

        return FeesResult(
            success=True,
            entity_id=statement_id,
            data={"total_minor": statement["monto_total_pagado_minor"]},
            message=f"Statement #{statement_id} closed. Total: {statement['monto_total_pagado_minor']} minor.",
        )

    def pay_statement(
        self,
        statement_id:   int,
        payment_date:   str,
        transaction_id: Optional[int] = None,
    ) -> FeesResult:
        """
        Marks a statement as 'pagado' and all its linked fees as 'pagado'.
        The actual cash transaction (egreso from your bank account) must be
        created separately via TransactionService and its id passed here.

        Args:
            statement_id:   The statement being paid.
            payment_date:   Payment date in 'YYYY-MM-DD'.
            transaction_id: Optional ID from TransactionService.create() that
                            represents the actual cash outflow.

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
        try:
            # Mark all fees in this statement as paid
            conn.execute(
                """
                UPDATE cuotas_credito
                SET estado = 'pagado'
                WHERE resumen_id = ? AND estado = 'en_resumen';
                """,
                (statement_id,),
            )

            # Mark the statement itself as paid
            conn.execute(
                """
                UPDATE resumenes_tarjeta
                SET estado = 'pagado', fecha_pago = ?
                WHERE id = ?;
                """,
                (validated_dt, statement_id),
            )
            conn.commit()

        except Exception:
            conn.rollback()
            raise

        return FeesResult(
            success=True,
            entity_id=statement_id,
            data={
                "statement_id":  statement_id,
                "payment_date":  validated_dt,
                "total_paid":    statement["monto_total_pagado_minor"],
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
        return (
            QueryBuilder("resumenes_tarjeta rt", include_deleted=True)
            .select("rt.*", "c.nombre AS account_name")
            .join("cuentas c", "c.id = rt.cuenta_id")
            .where("rt.id", statement_id)
            .ejecutar_uno(self._db.conn)
        )

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
        builder = (
            QueryBuilder("resumenes_tarjeta rt", include_deleted=True)
            .select("rt.*", "c.nombre AS account_name")
            .join("cuentas c", "c.id = rt.cuenta_id")
            .where("rt.cuenta_id", account_id)
            .where("rt.estado",    estado)
            .where("rt.anio",      year)
            .order("rt.anio",  "DESC")
            .order("rt.mes",   "DESC")
            .paginar(page, per_page)
        )
        return builder.ejecutar(self._db.conn)

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
        try:
            cur = conn.execute(
                """
                UPDATE cuotas_credito
                SET estado = 'omitido', notas = ?
                WHERE compra_id = ? AND estado = 'pendiente';
                """,
                (notes, purchase_id),
            )
            fees_cancelled = cur.rowcount

            conn.execute(
                "UPDATE compras_cuotas SET estado = 'cancelada', notas = ? WHERE id = ?;",
                (notes, purchase_id),
            )
            conn.commit()

        except Exception:
            conn.rollback()
            raise

        return FeesResult(
            success=True,
            entity_id=purchase_id,
            data={"fees_cancelled": fees_cancelled},
            message=(
                f"Purchase #{purchase_id} cancelled. "
                f"{fees_cancelled} pending fee(s) marked as omitido."
            ),
        )

