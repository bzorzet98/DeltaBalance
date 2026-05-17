"""
DeltaBalance — tests/test_transaction_service.py

Test suite for services/transaction_service.py.

Strategy:
    - Every test gets a fresh in-memory SQLite database (`:memory:`), initialized
      with the real schema.sql and seed.sql. This means tests run against the
      actual schema, not mocks — so a CHECK constraint failure or a missing FK
      will surface here, not in production.
    - Fixtures create the minimum data each test needs (accounts, categories).
    - Tests are grouped into classes by operation: Create, Transfer, Read, Update, Delete.
    - Error paths are tested alongside happy paths — a service that only works
      when inputs are correct is only half tested.

Run with:
    pytest tests/test_transaction_service.py -v
    pytest tests/test_transaction_service.py -v -k "test_create"   # filter by name
"""

import pytest
import sqlite3
from pathlib import Path

from db.database import DatabaseManager, from_minor
from services.transaction_service import (
    TransactionService,
    TransactionError,
    AccountNotFoundError,
    CategoryNotFoundError,
    CurrencyNotFoundError,
)


# =============================================================
# PATHS
# =============================================================

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "db" / "schema.sql"
SEED_PATH   = Path(__file__).resolve().parent.parent / "db" / "seed.sql"


# =============================================================
# FIXTURES
# =============================================================

@pytest.fixture
def db():
    """
    Provides an in-memory DatabaseManager for each test.
    The schema and seed are applied so the DB is fully initialized,
    including default currencies and categories from seed.sql.
    The connection is closed after the test automatically.
    """
    manager = DatabaseManager(db_path=":memory:")
    conn = manager.conectar()

    # Apply real schema
    with open(SCHEMA_PATH, encoding="utf-8") as f:
        conn.executescript(f.read())

    # Apply seed (currencies + default categories + efectivo account)
    with open(SEED_PATH, encoding="utf-8") as f:
        conn.executescript(f.read())

    yield manager
    manager.desconectar()


@pytest.fixture
def svc(db):
    """
    Provides a TransactionService wired to the in-memory DB fixture.
    """
    return TransactionService(db)


@pytest.fixture
def account_ars(db):
    """
    Creates a checking account in ARS and returns its ID.
    Represents a typical bank debit account.
    """
    account_id = db._execute(
        "INSERT INTO cuentas (nombre, tipo) VALUES ('Galicia ARS', 'debito');"
    )
    # Register initial balance (0 ARS) in cuentas_saldos
    ars_id = db._fetchone("SELECT id FROM monedas WHERE codigo = 'ARS';")["id"]
    db._execute(
        "INSERT INTO cuentas_saldos (cuenta_id, moneda_id, saldo_inicial_minor) VALUES (?, ?, 0);",
        (account_id, ars_id),
    )
    return account_id


@pytest.fixture
def account_usd(db):
    """
    Creates a USD account and returns its ID.
    Represents a foreign-currency savings account.
    """
    account_id = db._execute(
        "INSERT INTO cuentas (nombre, tipo) VALUES ('Galicia USD', 'debito');"
    )
    usd_id = db._fetchone("SELECT id FROM monedas WHERE codigo = 'USD';")["id"]
    db._execute(
        "INSERT INTO cuentas_saldos (cuenta_id, moneda_id, saldo_inicial_minor) VALUES (?, ?, 0);",
        (account_id, usd_id),
    )
    return account_id


@pytest.fixture
def category_supermarket(db):
    """Returns the ID of the 'Supermercado' category (loaded by seed)."""
    row = db._fetchone(
        "SELECT id FROM categorias WHERE subcategoria = 'Supermercado';"
    )
    assert row is not None, "Supermercado category missing — check seed.sql"
    return row["id"]


@pytest.fixture
def category_salary(db):
    """Returns the ID of the 'Sueldo' category (loaded by seed)."""
    row = db._fetchone(
        "SELECT id FROM categorias WHERE subcategoria = 'Sueldo';"
    )
    assert row is not None, "Sueldo category missing — check seed.sql"
    return row["id"]


@pytest.fixture
def category_transfer(db):
    """Returns the ID of the 'Autotransferencia' category (loaded by seed)."""
    row = db._fetchone(
        "SELECT id FROM categorias WHERE subcategoria = 'Autotransferencia';"
    )
    assert row is not None, "Autotransferencia category missing — check seed.sql"
    return row["id"]


# =============================================================
# HELPERS
# =============================================================

def make_expense(svc, account_id, category_id, amount=1000.0, date_str="2026-05-10"):
    """Creates a standard ARS expense. Reusable across multiple tests."""
    return svc.create(
        date_str=date_str,
        concept="Test expense",
        account_id=account_id,
        category_id=category_id,
        currency_code="ARS",
        amount=amount,
        movement_type="egreso",
    )


# =============================================================
# TEST: CREATE
# =============================================================

class TestCreate:

    def test_create_expense_returns_success(self, svc, account_ars, category_supermarket):
        """A standard expense should return a successful result with a valid ID."""
        result = make_expense(svc, account_ars, category_supermarket, amount=5000.0)

        assert result.success is True
        assert result.transaction_id is not None
        assert result.transaction_id > 0

    def test_create_income_returns_success(self, svc, account_ars, category_salary):
        """An income transaction should be created correctly."""
        result = svc.create(
            date_str="2026-05-01",
            concept="May salary",
            account_id=account_ars,
            category_id=category_salary,
            currency_code="ARS",
            amount=250000.0,
            movement_type="ingreso",
        )

        assert result.success is True
        assert result.data["movement_type"] == "ingreso"
        assert result.data["amount"] == 250000.0

    def test_create_stores_correct_minor_units(self, svc, db, account_ars, category_supermarket):
        """Amount must be stored as minor units (ARS has 2 decimals → × 100)."""
        result = make_expense(svc, account_ars, category_supermarket, amount=1234.56)

        row = db._fetchone(
            "SELECT monto_minor FROM transacciones WHERE id = ?;",
            (result.transaction_id,),
        )
        assert row["monto_minor"] == 123456  # 1234.56 × 100

    def test_create_with_tag_and_notes(self, svc, account_ars, category_supermarket):
        """Optional fields tag and notes should be persisted."""
        result = svc.create(
            date_str="2026-05-10",
            concept="Carrefour weekly",
            account_id=account_ars,
            category_id=category_supermarket,
            currency_code="ARS",
            amount=8000.0,
            movement_type="egreso",
            tag="weekly_shopping",
            notes="Included cleaning supplies",
        )

        assert result.success is True
        assert result.data["tag"] == "weekly_shopping"

    def test_create_usd_transaction(self, svc, account_usd, category_supermarket):
        """USD amounts should be stored with 2 decimals correctly."""
        result = svc.create(
            date_str="2026-05-10",
            concept="Online purchase",
            account_id=account_usd,
            category_id=category_supermarket,
            currency_code="USD",
            amount=49.99,
            movement_type="egreso",
        )

        assert result.success is True
        assert result.data["currency"] == "USD"

    def test_create_invalid_date_format_raises(self, svc, account_ars, category_supermarket):
        """Dates not in YYYY-MM-DD format should raise ValueError."""
        with pytest.raises(ValueError, match="Invalid date format"):
            svc.create(
                date_str="10/05/2026",   # wrong format
                concept="Bad date",
                account_id=account_ars,
                category_id=category_supermarket,
                currency_code="ARS",
                amount=100.0,
                movement_type="egreso",
            )

    def test_create_invalid_movement_type_raises(self, svc, account_ars, category_supermarket):
        """An unrecognized movement type should raise ValueError."""
        with pytest.raises(ValueError, match="Invalid movement type"):
            svc.create(
                date_str="2026-05-10",
                concept="Bad type",
                account_id=account_ars,
                category_id=category_supermarket,
                currency_code="ARS",
                amount=100.0,
                movement_type="gasto",   # not a valid type
            )

    def test_create_zero_amount_raises(self, svc, account_ars, category_supermarket):
        """Zero amount should be rejected — amounts must be positive."""
        with pytest.raises(ValueError, match="Amount must be positive"):
            svc.create(
                date_str="2026-05-10",
                concept="Zero amount",
                account_id=account_ars,
                category_id=category_supermarket,
                currency_code="ARS",
                amount=0.0,
                movement_type="egreso",
            )

    def test_create_negative_amount_raises(self, svc, account_ars, category_supermarket):
        """Negative amounts are rejected — direction is set by movement_type."""
        with pytest.raises(ValueError, match="Amount must be positive"):
            svc.create(
                date_str="2026-05-10",
                concept="Negative",
                account_id=account_ars,
                category_id=category_supermarket,
                currency_code="ARS",
                amount=-500.0,
                movement_type="egreso",
            )

    def test_create_empty_concept_raises(self, svc, account_ars, category_supermarket):
        """Empty or whitespace-only concept should raise TransactionError."""
        with pytest.raises(TransactionError, match="Concept cannot be empty"):
            svc.create(
                date_str="2026-05-10",
                concept="   ",
                account_id=account_ars,
                category_id=category_supermarket,
                currency_code="ARS",
                amount=100.0,
                movement_type="egreso",
            )

    def test_create_unknown_currency_raises(self, svc, account_ars, category_supermarket):
        """An unknown currency code should raise CurrencyNotFoundError."""
        with pytest.raises(CurrencyNotFoundError):
            svc.create(
                date_str="2026-05-10",
                concept="Bad currency",
                account_id=account_ars,
                category_id=category_supermarket,
                currency_code="XYZ",
                amount=100.0,
                movement_type="egreso",
            )

    def test_create_inactive_account_raises(self, svc, db, category_supermarket):
        """A transaction against an archived account should raise AccountNotFoundError."""
        # Create and immediately archive an account
        acc_id = db._execute(
            "INSERT INTO cuentas (nombre, tipo, activa) VALUES ('Closed', 'debito', 0);"
        )
        with pytest.raises(AccountNotFoundError):
            svc.create(
                date_str="2026-05-10",
                concept="Archived account",
                account_id=acc_id,
                category_id=category_supermarket,
                currency_code="ARS",
                amount=100.0,
                movement_type="egreso",
            )

    def test_create_unknown_category_raises(self, svc, account_ars):
        """A non-existent category ID should raise CategoryNotFoundError."""
        with pytest.raises(CategoryNotFoundError):
            svc.create(
                date_str="2026-05-10",
                concept="Bad category",
                account_id=account_ars,
                category_id=99999,
                currency_code="ARS",
                amount=100.0,
                movement_type="egreso",
            )


# =============================================================
# TEST: TRANSFER
# =============================================================

class TestTransfer:

    def test_transfer_creates_two_transactions(
        self, svc, db, account_ars, account_usd, category_transfer
    ):
        """An auto-transfer should produce exactly two transaction rows."""
        before = db._fetchone("SELECT COUNT(*) AS n FROM transacciones;")["n"]

        svc.create_transfer(
            date_str="2026-05-15",
            origin_account_id=account_ars,
            dest_account_id=account_usd,
            currency_code="ARS",
            amount=10000.0,
            category_id=category_transfer,
        )

        after = db._fetchone("SELECT COUNT(*) AS n FROM transacciones;")["n"]
        assert after - before == 2

    def test_transfer_creates_autotransferencia_link(
        self, svc, db, account_ars, account_usd, category_transfer
    ):
        """Both transaction IDs must be recorded in the autotransferencias table."""
        result = svc.create_transfer(
            date_str="2026-05-15",
            origin_account_id=account_ars,
            dest_account_id=account_usd,
            currency_code="ARS",
            amount=5000.0,
            category_id=category_transfer,
        )

        out_id = result.data["out_transaction_id"]
        in_id  = result.data["in_transaction_id"]

        link = db._fetchone(
            """
            SELECT * FROM autotransferencias
            WHERE transaccion_salida_id = ? AND transaccion_entrada_id = ?;
            """,
            (out_id, in_id),
        )
        assert link is not None

    def test_transfer_egreso_and_ingreso_types(
        self, svc, db, account_ars, account_usd, category_transfer
    ):
        """Outgoing leg should be 'egreso', incoming should be 'ingreso'."""
        result = svc.create_transfer(
            date_str="2026-05-15",
            origin_account_id=account_ars,
            dest_account_id=account_usd,
            currency_code="ARS",
            amount=2000.0,
            category_id=category_transfer,
        )

        out_row = db._fetchone(
            "SELECT tipo_movimiento FROM transacciones WHERE id = ?;",
            (result.data["out_transaction_id"],),
        )
        in_row = db._fetchone(
            "SELECT tipo_movimiento FROM transacciones WHERE id = ?;",
            (result.data["in_transaction_id"],),
        )

        assert out_row["tipo_movimiento"] == "egreso"
        assert in_row["tipo_movimiento"] == "ingreso"

    def test_transfer_same_account_raises(
        self, svc, account_ars, category_transfer
    ):
        """Transferring to the same account should raise TransactionError."""
        with pytest.raises(TransactionError, match="must be different"):
            svc.create_transfer(
                date_str="2026-05-15",
                origin_account_id=account_ars,
                dest_account_id=account_ars,
                currency_code="ARS",
                amount=1000.0,
                category_id=category_transfer,
            )


# =============================================================
# TEST: READ
# =============================================================

class TestRead:

    def test_get_returns_enriched_row(self, svc, account_ars, category_supermarket):
        """get() should return a row with account_name, category_name, currency_code."""
        result = make_expense(svc, account_ars, category_supermarket, amount=3000.0)
        row = svc.get(result.transaction_id)

        assert row is not None
        assert row["account_name"] == "Galicia ARS"
        assert row["category_name"] == "Supermercado"
        assert row["currency_code"] == "ARS"

    def test_get_nonexistent_returns_none(self, svc):
        """get() should return None for a non-existent ID."""
        row = svc.get(99999)
        assert row is None

    def test_list_returns_all_when_no_filters(
        self, svc, account_ars, category_supermarket
    ):
        """list() with no filters should return all created transactions."""
        for i in range(5):
            make_expense(svc, account_ars, category_supermarket, amount=100.0 * (i + 1))

        rows = svc.list()
        assert len(rows) >= 5

    def test_list_filters_by_account(
        self, svc, db, account_ars, account_usd, category_supermarket
    ):
        """list(account_id=X) should return only transactions for that account."""
        make_expense(svc, account_ars, category_supermarket, amount=1000.0)
        svc.create(
            date_str="2026-05-10",
            concept="USD purchase",
            account_id=account_usd,
            category_id=category_supermarket,
            currency_code="USD",
            amount=50.0,
            movement_type="egreso",
        )

        ars_rows = svc.list(account_id=account_ars)
        assert all(r["account_name"] == "Galicia ARS" for r in ars_rows)

    def test_list_filters_by_movement_type(
        self, svc, account_ars, category_supermarket, category_salary
    ):
        """list(movement_type='ingreso') should return only income rows."""
        make_expense(svc, account_ars, category_supermarket, amount=1000.0)
        svc.create(
            date_str="2026-05-01",
            concept="Salary",
            account_id=account_ars,
            category_id=category_salary,
            currency_code="ARS",
            amount=200000.0,
            movement_type="ingreso",
        )

        rows = svc.list(movement_type="ingreso")
        assert all(r["tipo_movimiento"] == "ingreso" for r in rows)
        assert len(rows) >= 1

    def test_list_filters_by_date_range(
        self, svc, account_ars, category_supermarket
    ):
        """list with date_from/date_to should respect the range boundaries."""
        make_expense(svc, account_ars, category_supermarket, date_str="2026-04-15", amount=100.0)
        make_expense(svc, account_ars, category_supermarket, date_str="2026-05-10", amount=200.0)
        make_expense(svc, account_ars, category_supermarket, date_str="2026-06-01", amount=300.0)

        rows = svc.list(date_from="2026-05-01", date_to="2026-05-31")
        dates = [r["fecha"] for r in rows]

        assert all("2026-05" in d for d in dates)
        assert "2026-04-15" not in dates
        assert "2026-06-01" not in dates

    def test_list_pagination(self, svc, account_ars, category_supermarket):
        """Pagination should split results across pages correctly."""
        for i in range(10):
            make_expense(svc, account_ars, category_supermarket, amount=100.0 + i)

        page1 = svc.list(per_page=4, page=1)
        page2 = svc.list(per_page=4, page=2)

        assert len(page1) == 4
        assert len(page2) >= 4

        # No overlap between pages
        ids_p1 = {r["id"] for r in page1}
        ids_p2 = {r["id"] for r in page2}
        assert ids_p1.isdisjoint(ids_p2)

    def test_count_matches_list_total(
        self, svc, account_ars, category_supermarket
    ):
        """count() should match the total rows list() would return without pagination."""
        for _ in range(7):
            make_expense(svc, account_ars, category_supermarket, amount=100.0)

        total = svc.count(account_id=account_ars, movement_type="egreso")
        rows  = svc.list(account_id=account_ars, movement_type="egreso", per_page=100)
        assert total == len(rows)

    def test_monthly_summary_totals(
        self, svc, account_ars, category_supermarket, category_salary
    ):
        """monthly_summary should correctly sum income and expenses for the month."""
        svc.create(
            date_str="2026-05-01",
            concept="Salary",
            account_id=account_ars,
            category_id=category_salary,
            currency_code="ARS",
            amount=100000.0,
            movement_type="ingreso",
        )
        make_expense(svc, account_ars, category_supermarket, amount=30000.0)
        make_expense(svc, account_ars, category_supermarket, amount=20000.0)

        rows = svc.monthly_summary(month=5, year=2026)
        ars_row = next((r for r in rows if r["currency_code"] == "ARS"), None)

        assert ars_row is not None
        assert ars_row["total_income_minor"]  == 10_000_000   # 100000 × 100
        assert ars_row["total_expense_minor"] == 5_000_000    # 50000 × 100
        assert ars_row["net_minor"]           == 5_000_000

    def test_monthly_summary_invalid_month_raises(self, svc):
        """A month outside 1–12 should raise ValueError."""
        with pytest.raises(ValueError, match="Month must be between"):
            svc.monthly_summary(month=13, year=2026)


# =============================================================
# TEST: UPDATE
# =============================================================

class TestUpdate:

    def test_update_concept(self, svc, db, account_ars, category_supermarket):
        """Updating the concept should persist the new value."""
        result = make_expense(svc, account_ars, category_supermarket)
        svc.update(result.transaction_id, concept="Updated concept")

        row = db._fetchone(
            "SELECT concepto FROM transacciones WHERE id = ?;",
            (result.transaction_id,),
        )
        assert row["concepto"] == "Updated concept"

    def test_update_amount_and_currency(self, svc, db, account_ars, category_supermarket):
        """Amount and currency can be updated together."""
        result = make_expense(svc, account_ars, category_supermarket, amount=100.0)
        svc.update(result.transaction_id, amount=500.0, currency_code="ARS")

        row = db._fetchone(
            "SELECT monto_minor FROM transacciones WHERE id = ?;",
            (result.transaction_id,),
        )
        assert row["monto_minor"] == 50000  # 500.00 × 100

    def test_update_amount_without_currency_raises(self, svc, account_ars, category_supermarket):
        """Passing amount without currency_code should raise ValueError."""
        result = make_expense(svc, account_ars, category_supermarket)
        with pytest.raises(ValueError, match="must be updated together"):
            svc.update(result.transaction_id, amount=999.0)

    def test_update_no_fields_returns_false(self, svc, account_ars, category_supermarket):
        """Calling update with no fields should return success=False."""
        result = make_expense(svc, account_ars, category_supermarket)
        update_result = svc.update(result.transaction_id)

        assert update_result.success is False

    def test_update_nonexistent_raises(self, svc):
        """Updating a non-existent transaction should raise TransactionError."""
        with pytest.raises(TransactionError, match="not found"):
            svc.update(99999, concept="Ghost")

    def test_update_clears_tag_with_empty_string(self, svc, db, account_ars, category_supermarket):
        """Passing tag='' should clear the tag (store NULL)."""
        result = svc.create(
            date_str="2026-05-10",
            concept="Tagged",
            account_id=account_ars,
            category_id=category_supermarket,
            currency_code="ARS",
            amount=100.0,
            movement_type="egreso",
            tag="some_tag",
        )
        svc.update(result.transaction_id, tag="")

        row = db._fetchone(
            "SELECT tag FROM transacciones WHERE id = ?;",
            (result.transaction_id,),
        )
        assert row["tag"] is None


# =============================================================
# TEST: DELETE
# =============================================================

class TestDelete:

    def test_delete_removes_transaction(self, svc, db, account_ars, category_supermarket):
        """After delete, the transaction should not exist in the DB."""
        result = make_expense(svc, account_ars, category_supermarket)
        svc.delete(result.transaction_id)

        row = db._fetchone(
            "SELECT id FROM transacciones WHERE id = ?;",
            (result.transaction_id,),
        )
        assert row is None

    def test_delete_nonexistent_returns_false(self, svc):
        """Deleting a non-existent ID should return success=False, not raise."""
        result = svc.delete(99999)
        assert result.success is False

    def test_delete_transfer_removes_autotransferencia_link(
        self, svc, db, account_ars, account_usd, category_transfer
    ):
        """Deleting one leg of a transfer should also remove the autotransferencias row."""
        transfer = svc.create_transfer(
            date_str="2026-05-15",
            origin_account_id=account_ars,
            dest_account_id=account_usd,
            currency_code="ARS",
            amount=5000.0,
            category_id=category_transfer,
        )
        out_id = transfer.data["out_transaction_id"]
        svc.delete(out_id)

        link = db._fetchone(
            "SELECT id FROM autotransferencias WHERE transaccion_salida_id = ?;",
            (out_id,),
        )
        assert link is None

    def test_delete_result_contains_id(self, svc, account_ars, category_supermarket):
        """The delete result should carry the deleted transaction's ID."""
        result = make_expense(svc, account_ars, category_supermarket)
        delete_result = svc.delete(result.transaction_id)

        assert delete_result.transaction_id == result.transaction_id

    def test_amount_display_helper(self, svc, account_ars, category_supermarket):
        """amount_display() should convert minor units back to a readable float."""
        result = make_expense(svc, account_ars, category_supermarket, amount=1234.56)
        row = svc.get(result.transaction_id)

        displayed = svc.amount_display(row)
        assert abs(displayed - 1234.56) < 0.001  # float tolerance
