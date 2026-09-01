"""
DeltaBalance — services/transaction_service.py

Purpose:
    Orchestrates all transaction-related workflows. Acts as the boundary between
    the UI layer and the database layer — the UI never touches DatabaseManager
    or QueryBuilder directly for transaction operations.

    Responsibilities:
    - Validate inputs before they reach the database.
    - Coordinate multi-step operations (e.g. auto-transfers create two rows
      and one linking record in a single atomic transaction).
    - Convert between user-facing floats and internal minor units.
    - Return structured result dicts so the UI has everything it needs
      without making additional DB calls.

    What this service does NOT do:
    - It does not format output for display (that belongs in cli/formatter.py).
    - It does not know about cuotas, sueldos, or deudas (those have their
      own services).
    - It does not build SQL strings (that belongs in DatabaseManager /
      QueryBuilder).
"""

import sqlite3
from datetime import date, datetime
from typing import Optional, Any
from dataclasses import dataclass, field

from db.database import DatabaseManager, to_minor, from_minor
from db.query_builder import QueryBuilder
from repositories.transacciones_repository import TransaccionesRepository


# =============================================================
# EXCEPTIONS
# =============================================================

class TransactionError(Exception):
    """Raised when a transaction operation fails due to a business rule violation."""


class AccountNotFoundError(TransactionError):
    """Raised when a referenced account does not exist or is inactive."""


class CategoryNotFoundError(TransactionError):
    """Raised when a referenced category does not exist."""


class CurrencyNotFoundError(TransactionError):
    """Raised when a referenced currency code does not exist in the monedas table."""


# =============================================================
# RESULT TYPE
# =============================================================

@dataclass
class TransactionResult:
    """
    Standard result object returned by financial service methods.
    """

    success: bool
    transaction_id: Optional[int] = None
    data: dict[str, Any] = field(default_factory=dict)
    message: str = ""

    def __repr__(self) -> str:
        return (
            f"TransactionResult(success={self.success}, "
            f"id={self.transaction_id}, message='{self.message}')"
        )


# =============================================================
# SERVICE
# =============================================================

class TransactionService:
    """
    Entry point for all transaction operations in DeltaBalance.

    Usage:
        db  = DatabaseManager()
        db.initialize()
        svc = TransactionService(db)

        result = svc.create(
            date="2026-05-10",
            concept="Supermarket run",
            account_id=1,
            category_id=5,
            currency_code="ARS",
            amount=15400.00,
            movement_type="egreso",
        )
        print(result.transaction_id)
    """

    def __init__(self, db: DatabaseManager):
        """
        Initializes the service with an active DatabaseManager instance.

        Args:
            db: An initialized DatabaseManager. The service assumes the DB
                is already set up (schema + seed applied).
        """
        self._db = db
        # Constructor sigue tomando solo `db` (no un TransaccionesRepository
        # aparte) para no romper a quien ya instancia TransactionService(db)
        # hoy (ver tests/conftest.py y el docstring de esta clase) — el
        # repositorio se arma internamente.
        self._repo = TransaccionesRepository(db)

    # ----------------------------------------------------------
    # INTERNAL HELPERS
    # ----------------------------------------------------------

    def _get_currency(self, code: str) -> sqlite3.Row:
        """
        Fetches a currency row by its code (e.g. 'ARS', 'USD').
        Raises CurrencyNotFoundError if the code does not exist in monedas.

        Args:
            code: ISO-like currency code stored in the monedas table.

        Returns:
            sqlite3.Row with id, codigo, simbolo, decimales.
        """
        row = self._db.fetchone(
            "SELECT * FROM monedas WHERE codigo = ?;", (code.upper(),)
        )
        if row is None:
            raise CurrencyNotFoundError(
                f"Currency '{code}' not found. Check the monedas table."
            )
        return row

    def _get_account(self, account_id: int) -> sqlite3.Row:
        """
        Fetches an active account by ID.
        Raises AccountNotFoundError if it does not exist or is archived.

        Args:
            account_id: Primary key of the cuentas table.

        Returns:
            sqlite3.Row for the account.
        """
        row = self._db.fetchone(
            "SELECT * FROM cuentas WHERE id = ? AND activa = 1;", (account_id,)
        )
        if row is None:
            raise AccountNotFoundError(
                f"Account id={account_id} not found or is inactive."
            )
        return row

    def _get_category(self, category_id: int) -> sqlite3.Row:
        """
        Fetches a category by ID.
        Raises CategoryNotFoundError if it does not exist.

        Args:
            category_id: Primary key of the categorias table.

        Returns:
            sqlite3.Row for the category.
        """
        row = self._db.fetchone(
            "SELECT * FROM categorias WHERE id = ?;", (category_id,)
        )
        if row is None:
            raise CategoryNotFoundError(
                f"Category id={category_id} not found."
            )
        return row

    @staticmethod
    def _validate_date(date_str: str) -> str:
        """
        Validates and normalizes a date string to 'YYYY-MM-DD' format.
        Accepts date objects or strings in YYYY-MM-DD format.
        Raises ValueError for anything else.

        Args:
            date_str: Date as string 'YYYY-MM-DD' or a date/datetime object.

        Returns:
            Validated string in 'YYYY-MM-DD' format.
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
    def _validate_movement_type(movement_type: str) -> str:
        """
        Validates the movement type against the schema CHECK constraint.
        Raises ValueError for invalid types.

        Valid values mirror the transacciones.tipo_movimiento CHECK:
            'ingreso', 'egreso', 'movimiento'

        Args:
            movement_type: The movement type string from the caller.

        Returns:
            The validated lowercase string.
        """
        valid = {"ingreso", "egreso", "movimiento"}
        normalized = movement_type.lower().strip()
        if normalized not in valid:
            raise ValueError(
                f"Invalid movement type '{movement_type}'. "
                f"Must be one of: {', '.join(sorted(valid))}."
            )
        return normalized

    @staticmethod
    def _validate_amount(amount: float) -> float:
        """
        Ensures the amount is a positive number.
        The sign (income vs expense) is determined by tipo_movimiento, not the amount.

        Args:
            amount: Monetary amount as a float.

        Returns:
            The validated amount.

        Raises:
            ValueError if the amount is zero or negative.
        """
        if amount <= 0:
            raise ValueError(
                f"Amount must be positive. Received: {amount}. "
                "Sign is determined by movement_type, not the amount."
            )
        return amount

    # ----------------------------------------------------------
    # CREATE
    # ----------------------------------------------------------

    # Migrado a TransaccionesRepository (Fase 2, paso 3): el repositorio
    # ahora expone crear(..., conn=...) para participar de una transacción
    # externa. Cuando autocommit=False (create_transfer() llamando dos veces
    # dentro de self._db.transaction()), se le pasa self._db.conn para que
    # el INSERT no comitee por su cuenta — igual que antes con
    # self._db.execute(..., autocommit=False).
    def create(
        self,
        date_str:      str,
        concept:       str,
        account_id:    int,
        category_id:   int,
        currency_code: str,
        amount:        float,
        movement_type: str,
        tag:           Optional[str] = None,
        notes:         Optional[str] = None,
        autocommit: bool = True,
    ) -> TransactionResult:
        """
        Creates a single transaction (income, expense, or generic movement).
        This is the core method — all other create methods call this internally.

        Validates all inputs before writing. The amount is always stored as a
        positive integer (minor units); the direction is encoded in movement_type.

        Args:
            date_str:      Transaction date in 'YYYY-MM-DD' format.
            concept:       Short description. E.g. 'Supermarket', 'Salary May'.
            account_id:    ID of the account this transaction belongs to.
            category_id:   ID of the category for classification.
            currency_code: Currency code. E.g. 'ARS', 'USD', 'USDT'.
            amount:        Positive float. E.g. 15400.00
            movement_type: 'ingreso', 'egreso', or 'movimiento'.
            tag:           Optional free-text label. E.g. 'sueldo', 'pago_tarjeta'.
            notes:         Optional longer description or memo.

        Returns:
            TransactionResult with success=True and the new transaction's id.

        Raises:
            TransactionError subclasses for validation failures.
            sqlite3.IntegrityError for FK violations not caught above.
        """
        # --- Validate ---
        validated_date  = self._validate_date(date_str)
        validated_type  = self._validate_movement_type(movement_type)
        validated_amount = self._validate_amount(amount)

        if not concept or not concept.strip():
            raise TransactionError("Concept cannot be empty.")

        currency = self._get_currency(currency_code)
        self._get_account(account_id)     # raises if not found
        self._get_category(category_id)   # raises if not found

        minor = to_minor(validated_amount, currency["decimales"])

        # --- Write ---
        if autocommit:
            tx_id = self._repo.crear(
                validated_date, concept.strip(), account_id, category_id,
                currency["id"], validated_type, minor, tag, notes,
            )
        else:
            tx_id = self._repo.crear(
                validated_date, concept.strip(), account_id, category_id,
                currency["id"], validated_type, minor, tag, notes,
                conn=self._db.conn,
            )

        return TransactionResult(
            success=True,
            transaction_id=tx_id,
            data={
                "id":            tx_id,
                "date":          validated_date,
                "concept":       concept.strip(),
                "account_id":    account_id,
                "category_id":   category_id,
                "currency":      currency_code.upper(),
                "amount":        validated_amount,
                "amount_minor":  minor,
                "movement_type": validated_type,
                "tag":           tag,
            },
            message=f"Transaction #{tx_id} created successfully.",
        )

    def create_transfer(
        self,
        date_str:          str,
        origin_account_id: int,
        dest_account_id:   int,
        currency_code:     str,
        amount:            float,
        category_id:       int,
        notes:             Optional[str] = None,
    ) -> TransactionResult:
        """
        Creates an auto-transfer between two accounts owned by the user.
        Produces two transaction rows (egreso from origin, ingreso to destination)
        and one linking row in autotransferencias (via
        TransaccionesRepository.crear_autotransferencia() — added when this
        method's docstring was found to claim it wrote that row when it
        actually didn't; see docs/DATA_MODEL_DECISIONS.md sección 13). The
        whole operation is atomic — if anything fails, none of the three
        rows (two transactions + the link) are persisted.

        Args:
            date_str:          Transfer date in 'YYYY-MM-DD'.
            origin_account_id: Account money moves OUT of.
            dest_account_id:   Account money moves INTO.
            currency_code:     Currency code for both legs.
            amount:            Positive float amount to transfer.
            category_id:       Should be the 'Autotransferencia' category.
            notes:             Optional memo.

        Returns:
            TransactionResult with both transaction IDs in the data dict.

        Raises:
            TransactionError if origin and destination are the same account.
        """
        if origin_account_id == dest_account_id:
            raise TransactionError(
                "Origin and destination accounts must be different."
            )

        # Validate both accounts exist upfront (before any writes)
        self._get_account(origin_account_id)
        self._get_account(dest_account_id)

        conn = self._db.conn

        with self._db.transaction():
            # Egreso from origin
            out_result = self.create(
                date_str=date_str,
                concept="Auto-transfer (out)",
                account_id=origin_account_id,
                category_id=category_id,
                currency_code=currency_code,
                amount=amount,
                movement_type="egreso",
                tag="autotransferencia",
                notes=notes,
                autocommit=False,
            )

            # Ingreso to destination
            in_result = self.create(
                date_str=date_str,
                concept="Auto-transfer (in)",
                account_id=dest_account_id,
                category_id=category_id,
                currency_code=currency_code,
                amount=amount,
                movement_type="ingreso",
                tag="autotransferencia",
                autocommit=False,
            )

            # Formal link between the two transactions above
            self._repo.crear_autotransferencia(
                transaccion_salida_id=out_result.transaction_id,
                transaccion_entrada_id=in_result.transaction_id,
                notas=notes,
                conn=conn,
            )

        return TransactionResult(
            success=True,
            transaction_id=out_result.transaction_id,
            data={
                "out_transaction_id": out_result.transaction_id,
                "in_transaction_id":  in_result.transaction_id,
                "origin_account_id":  origin_account_id,
                "dest_account_id":    dest_account_id,
                "currency":           currency_code.upper(),
                "amount":             amount,
            },
            message=(
                f"Transfer of {amount} {currency_code.upper()} "
                f"from account #{origin_account_id} "
                f"to account #{dest_account_id} registered."
            ),
        )

    # ----------------------------------------------------------
    # READ
    # ----------------------------------------------------------

    # Migrado a TransaccionesRepository (Fase 2, paso 3): usa
    # obtener_enriquecida(), que reproduce exactamente esta misma query con
    # JOINs. incluir_eliminadas=True replica el include_deleted=True que
    # esta query tenía hardcodeado (get() siempre encuentra transacciones
    # eliminadas, a diferencia de list_transactions()).
    def get(self, transaction_id: int) -> Optional[sqlite3.Row]:
        """
        Fetches a single transaction by its primary key, enriched with
        account name, category name, and currency code via JOINs.

        Args:
            transaction_id: Primary key in the transacciones table.

        Returns:
            sqlite3.Row if found, None if not.
        """
        return self._repo.obtener_enriquecida(transaction_id, incluir_eliminadas=True)

    # Migrado a TransaccionesRepository (Fase 2, paso 3): usa
    # listar_enriquecida(), que reproduce esta misma query (filtros,
    # paginación, JOINs y subconjunto curado de columnas). No se pasa
    # incluir_eliminadas — su default (False) replica el comportamiento
    # anterior, que siempre excluía eliminadas y no tenía forma de pedir lo
    # contrario.
    def list_transactions(
        self,
        account_id:    Optional[int] = None,
        category_id:   Optional[int] = None,
        currency_code: Optional[str] = None,
        movement_type: Optional[str] = None,
        date_from:     Optional[str] = None,
        date_to:       Optional[str] = None,
        tag:           Optional[str] = None,
        page:          int = 1,
        per_page:      int = 50,
    ) -> list[sqlite3.Row]:
        """
        Returns a paginated list of transactions with optional filters.
        All filter arguments are optional — omitting them returns everything.
        Filters are AND-combined.

        Args:
            account_id:    Filter by account. None = all accounts.
            category_id:   Filter by category. None = all categories.
            currency_code: Filter by currency code (e.g. 'ARS'). None = all.
            movement_type: Filter by type ('ingreso', 'egreso', 'movimiento'). None = all.
            date_from:     Inclusive start date 'YYYY-MM-DD'. None = no lower bound.
            date_to:       Inclusive end date 'YYYY-MM-DD'. None = no upper bound.
            tag:           Filter by exact tag value. None = all.
            page:          Page number, 1-indexed. Default 1.
            per_page:      Rows per page. Default 50.

        Returns:
            List of sqlite3.Row, ordered by date DESC then id DESC.
        """
        currency_id = None
        if currency_code:
            currency_id = self._get_currency(currency_code)["id"]

        return self._repo.listar_enriquecida(
            cuenta_id=account_id,
            categoria_id=category_id,
            moneda_id=currency_id,
            tipo_movimiento=movement_type,
            fecha_desde=date_from,
            fecha_hasta=date_to,
            tag=tag,
            pagina=page,
            por_pagina=per_page,
        )

    def count_transactions(
        self,
        account_id:    Optional[int] = None,
        category_id:   Optional[int] = None,
        currency_code: Optional[str] = None,
        movement_type: Optional[str] = None,
        date_from:     Optional[str] = None,
        date_to:       Optional[str] = None,
        tag:           Optional[str] = None,
    ) -> int:
        """
        Returns the total number of transactions matching the given filters.
        Use together with list() to build pagination controls in the UI.

        Args:
            Same optional filters as list(), without page/per_page.

        Returns:
            Integer count of matching transactions.
        """
        currency_id = None
        if currency_code:
            currency_id = self._get_currency(currency_code)["id"]

        return (
            QueryBuilder("transacciones t")
            .join("monedas m", "m.id = t.moneda_id")
            .where("t.cuenta_id",       account_id)
            .where("t.categoria_id",    category_id)
            .where("t.moneda_id",       currency_id)
            .where("t.tipo_movimiento", movement_type)
            .where("t.fecha",           date_from, ">=")
            .where("t.fecha",           date_to,   "<=")
            .where("t.tag",             tag)
            .contar(self._db.conn)
        )

    def monthly_summary(self, month: int, year: int) -> list[sqlite3.Row]:
        """
        Returns total income, total expenses, and net balance for a given month,
        broken down by currency. This is the data behind the dashboard header.

        Args:
            month: Integer 1–12.
            year:  Four-digit integer year.

        Returns:
            List of rows, one per currency found, with columns:
                currency_code, currency_symbol, total_income_minor,
                total_expense_minor, net_minor
        """
        if not (1 <= month <= 12):
            raise ValueError(f"Month must be between 1 and 12. Received: {month}")


        if month == 12:
            next_month = date(year + 1, 1, 1)
        else:
            next_month = date(year, month + 1, 1)

        start_date = f"{year:04d}-{month:02d}-01"
        end_date = next_month.isoformat()

        return self._db.fetchall(
            """
            SELECT
                m.codigo        AS currency_code,
                m.simbolo       AS currency_symbol,
                m.decimales,
                SUM(CASE WHEN t.tipo_movimiento = 'ingreso'
                         THEN t.monto_minor ELSE 0 END) AS total_income_minor,
                SUM(CASE WHEN t.tipo_movimiento = 'egreso'
                         THEN t.monto_minor ELSE 0 END) AS total_expense_minor,
                SUM(CASE WHEN t.tipo_movimiento = 'ingreso'
                         THEN  t.monto_minor
                         WHEN t.tipo_movimiento = 'egreso'
                         THEN -t.monto_minor
                         ELSE  0 END)                   AS net_minor
            FROM transacciones t
            JOIN monedas m ON m.id = t.moneda_id
            WHERE t.fecha >= ?
            AND t.fecha < ?
            AND t.deleted_at IS NULL
            GROUP BY m.id
            ORDER BY m.codigo;
            """,
            (start_date, end_date),
        )

    # ----------------------------------------------------------
    # UPDATE
    # ----------------------------------------------------------

    # Migrado a TransaccionesRepository (Fase 2, paso 3): usa
    # actualizar() con el sentinel NO_CAMBIAR. Solo se arma un kwarg por
    # campo que efectivamente cambia — los que no se pasan simplemente no
    # entran en el dict, así que el repositorio los deja en NO_CAMBIAR (sin
    # tocar). tag/notes siguen aceptando '' para borrar: `tag or None`
    # calcula el None explícito que el repositorio ahora sabe interpretar
    # como "escribir NULL a propósito" en vez de "no tocar".
    def update(
        self,
        transaction_id: int,
        date_str:       Optional[str]   = None,
        concept:        Optional[str]   = None,
        amount:         Optional[float] = None,
        currency_code:  Optional[str]   = None,
        category_id:    Optional[int]   = None,
        tag:            Optional[str]   = None,
        notes:          Optional[str]   = None,
    ) -> TransactionResult:
        """
        Updates one or more fields of an existing transaction.
        Only the fields explicitly passed (non-None) are modified.
        Amount and currency must be updated together if either changes.

        Args:
            transaction_id: The transaction to modify.
            date_str:       New date in 'YYYY-MM-DD'. None = no change.
            concept:        New description. None = no change.
            amount:         New positive amount. Must pass currency_code too.
            currency_code:  New currency. Must pass amount too.
            category_id:    New category ID. None = no change.
            tag:            New tag. Pass empty string '' to clear it.
            notes:          New notes. Pass empty string '' to clear them.

        Returns:
            TransactionResult with success=True if at least one field changed.

        Raises:
            TransactionError if the transaction does not exist.
            ValueError if amount is given without currency_code or vice versa.
        """
        existing = self.get(transaction_id)
        if existing is None:
            raise TransactionError(
                f"Transaction id={transaction_id} not found."
            )

        # Amount and currency must travel together
        if (amount is None) != (currency_code is None):
            raise ValueError(
                "amount and currency_code must be updated together. "
                "Provide both or neither."
            )

        campos_repo: dict[str, Any] = {}

        if date_str is not None:
            campos_repo["fecha"] = self._validate_date(date_str)

        if concept is not None:
            if not concept.strip():
                raise TransactionError("Concept cannot be empty.")
            campos_repo["concepto"] = concept.strip()

        if category_id is not None:
            self._get_category(category_id)  # validate existence
            campos_repo["categoria_id"] = category_id

        if amount is not None and currency_code is not None:
            self._validate_amount(amount)
            currency = self._get_currency(currency_code)
            campos_repo["moneda_id"] = currency["id"]
            campos_repo["monto_minor"] = to_minor(amount, currency["decimales"])

        # tag and notes accept empty strings to clear the value
        if tag is not None:
            campos_repo["tag"] = tag or None

        if notes is not None:
            campos_repo["notas"] = notes or None

        if not campos_repo:
            return TransactionResult(
                success=False,
                transaction_id=transaction_id,
                message="No fields to update were provided.",
            )

        self._repo.actualizar(transaction_id, **campos_repo)

        return TransactionResult(
            success=True,
            transaction_id=transaction_id,
            message=f"Transaction #{transaction_id} updated ({len(campos_repo)} field(s) changed).",
        )

    # ----------------------------------------------------------
    # DELETE
    # ----------------------------------------------------------

    def get_delete_warnings(self, transaction_id: int) -> dict:
        """
        Comprueba si transaction_id está vinculada a una autotransferencia
        (autotransferencias.transaccion_salida_id/transaccion_entrada_id) o
        es el origen de un movimiento de ahorro
        (movimientos_activo.transaccion_id) — para que la UI pueda avisar
        de qué más se ve afectado ANTES de borrar. No bloquea nada por sí
        misma (delete() sigue funcionando igual con o sin vínculos) — solo
        informa, la decisión de mostrar una confirmación es de la UI.

        Sin repositorio propio para `autotransferencias` (no existe ningún
        bloque de la Fase 2 dedicado a esa tabla, ver
        docs/DATA_MODEL_DECISIONS.md sección 13) — se consulta directo con
        self._db.fetchone(), mismo criterio que _get_currency()/_get_account()
        de este mismo service para tablas sin repositorio propio.

        Args:
            transaction_id: La transacción a chequear.

        Returns:
            dict {
                "es_autotransferencia": bool,
                "transaccion_par_id": Optional[int] (la otra pata del par,
                    si es_autotransferencia),
                "es_origen_ahorro": bool,
            }
        """
        fila_transferencia = self._db.fetchone(
            """
            SELECT transaccion_salida_id, transaccion_entrada_id
            FROM autotransferencias
            WHERE transaccion_salida_id = ? OR transaccion_entrada_id = ?;
            """,
            (transaction_id, transaction_id),
        )
        fila_ahorro = self._db.fetchone(
            "SELECT id FROM movimientos_activo WHERE transaccion_id = ?;",
            (transaction_id,),
        )

        transaccion_par_id = None
        if fila_transferencia is not None:
            transaccion_par_id = (
                fila_transferencia["transaccion_entrada_id"]
                if fila_transferencia["transaccion_salida_id"] == transaction_id
                else fila_transferencia["transaccion_salida_id"]
            )

        return {
            "es_autotransferencia": fila_transferencia is not None,
            "transaccion_par_id": transaccion_par_id,
            "es_origen_ahorro": fila_ahorro is not None,
        }

    def delete(self, transaction_id: int) -> TransactionResult:
        """
        Soft-deletes a transaction by setting deleted_at to the current timestamp.

        Args:
            transaction_id: The transaction to delete.

        Returns:
            TransactionResult with success=True if deleted, False if not found.
        """
        # Migrado a TransaccionesRepository (Fase 2): la existencia solo se
        # chequea con `is None`, no se lee ningún campo enriquecido, así que
        # obtener_por_id() alcanza. incluir_eliminadas=True replica el
        # include_deleted=True que tenía self.get() acá — un transaction_id
        # ya eliminado sigue "existiendo" para este chequeo (delete() sobre
        # algo ya eliminado es idempotente, no un "not found").
        existing = self._repo.obtener_por_id(transaction_id, incluir_eliminadas=True)
        if existing is None:
            return TransactionResult(
                success=False,
                transaction_id=transaction_id,
                message=f"Transaction id={transaction_id} not found — nothing deleted.",
            )

        conn = self._db.conn
        try:
            self._repo.eliminar(transaction_id)

        except Exception:
            conn.rollback()
            raise

        return TransactionResult(
            success=True,
            transaction_id=transaction_id,
            message=f"Transaction #{transaction_id} deleted.",
        )