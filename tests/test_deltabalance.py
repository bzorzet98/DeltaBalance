"""
DeltaBalance — tests/test_deltabalance.py

Full test suite covering:
    - utils/money.py          → amount_display()
    - db/query_builder.py     → QueryBuilder (build, filters, pagination, soft delete)
    - services/transaction_service.py → TransactionService (create, transfer, read, update, delete)

Strategy:
    Every test gets a fresh in-memory SQLite DB initialized with the real
    schema.sql and seed.sql. No mocks — real schema constraints surface here.

Run:
    pytest tests/test_deltabalance.py -v
    pytest tests/test_deltabalance.py -v -k "TestDelete"
"""

import pytest
import sqlite3
from pathlib import Path
from datetime import date

from db.database import DatabaseManager, to_minor, from_minor
from db.query_builder import QueryBuilder
from services.transaction_service import (
    TransactionService,
    TransactionResult,
    TransactionError,
    AccountNotFoundError,
    CategoryNotFoundError,
    CurrencyNotFoundError,
)
from utils.money import amount_display


# =============================================================
# PATHS
# =============================================================

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "db" / "schema.sql"
SEED_PATH   = Path(__file__).resolve().parent.parent / "db" / "seed.sql"


# =============================================================
# SHARED FIXTURES
# =============================================================

@pytest.fixture
def db():
    """
    Fresh in-memory DatabaseManager for each test.
    Schema + seed are applied so currencies, categories, and the
    default efectivo account are all present.
    """
    manager = DatabaseManager(db_path=":memory:")
    conn = manager.conectar()
    with open(SCHEMA_PATH, encoding="utf-8") as f:
        conn.executescript(f.read())
    with open(SEED_PATH, encoding="utf-8") as f:
        conn.executescript(f.read())
    yield manager
    manager.desconectar()


@pytest.fixture
def svc(db):
    """TransactionService wired to the in-memory DB."""
    return TransactionService(db)


@pytest.fixture
def account_ars(db):
    """Active ARS debit account. Returns its ID."""
    acc_id = db.execute(
        "INSERT INTO cuentas (nombre, tipo) VALUES ('Galicia ARS', 'debito');"
    )
    ars_id = db.fetchone("SELECT id FROM monedas WHERE codigo = 'ARS';")["id"]
    db.execute(
        "INSERT INTO cuentas_saldos (cuenta_id, moneda_id, saldo_inicial_minor) VALUES (?, ?, 0);",
        (acc_id, ars_id),
    )
    return acc_id


@pytest.fixture
def account_usd(db):
    """Active USD debit account. Returns its ID."""
    acc_id = db.execute(
        "INSERT INTO cuentas (nombre, tipo) VALUES ('Galicia USD', 'debito');"
    )
    usd_id = db.fetchone("SELECT id FROM monedas WHERE codigo = 'USD';")["id"]
    db.execute(
        "INSERT INTO cuentas_saldos (cuenta_id, moneda_id, saldo_inicial_minor) VALUES (?, ?, 0);",
        (acc_id, usd_id),
    )
    return acc_id


@pytest.fixture
def cat_supermarket(db):
    """ID of the 'Supermercado' category from seed."""
    row = db.fetchone("SELECT id FROM categorias WHERE subcategoria = 'Supermercado';")
    assert row, "Supermercado missing — check seed.sql"
    return row["id"]


@pytest.fixture
def cat_salary(db):
    """ID of the 'Sueldo' category from seed."""
    row = db.fetchone("SELECT id FROM categorias WHERE subcategoria = 'Sueldo';")
    assert row, "Sueldo missing — check seed.sql"
    return row["id"]


@pytest.fixture
def cat_transfer(db):
    """ID of the 'Autotransferencia' category from seed."""
    row = db.fetchone("SELECT id FROM categorias WHERE subcategoria = 'Autotransferencia';")
    assert row, "Autotransferencia missing — check seed.sql"
    return row["id"]


# =============================================================
# HELPER
# =============================================================

def make_expense(svc, account_id, category_id, amount=1000.0, date_str="2026-05-10"):
    """One-liner to create a standard ARS egreso. Reused across many tests."""
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
# TEST: utils/money.py — amount_display
# =============================================================

class TestAmountDisplay:

    def test_basic_ars(self):
        """Standard ARS amount with default symbol."""
        assert amount_display(123456) == "$1,234.56"

    def test_zero(self):
        """Zero minor units should display as $0.00."""
        assert amount_display(0) == "$0.00"

    def test_custom_symbol(self):
        """Custom currency symbol should appear as prefix."""
        assert amount_display(100000, decimals=2, currency_symbol="U$S") == "U$S1,000.00"

    def test_zero_decimals(self):
        """CLP has 0 decimals — minor units equal the face value."""
        assert amount_display(5000, decimals=0, currency_symbol="CLP$") == "CLP$5,000.00"

    def test_eight_decimals_btc(self):
        """BTC has 8 decimals — 1 BTC = 100_000_000 minor units."""
        assert amount_display(100_000_000, decimals=8, currency_symbol="BTC") == "BTC1.00"

    def test_thousands_separator(self):
        """Large amounts should include thousands separators."""
        result = amount_display(1_000_000_00)   # 1,000,000.00 ARS
        assert "1,000,000.00" in result

    def test_negative_minor(self):
        """Negative minor units (if ever passed) should display with minus sign."""
        result = amount_display(-5000)
        assert "-" in result


# =============================================================
# TEST: db/query_builder.py — QueryBuilder
# =============================================================

class TestQueryBuilderBuild:
    """Tests that verify the SQL string produced by build(), without executing."""

    def test_default_select_star(self):
        """No .select() call → SELECT *."""
        sql, _ = QueryBuilder("cuentas", include_deleted=True).build()
        assert "SELECT *" in sql

    def test_custom_columns(self):
        """Specified columns appear in SELECT."""
        sql, _ = (
            QueryBuilder("cuentas", include_deleted=True)
            .select("id", "nombre")
            .build()
        )
        assert "SELECT id, nombre" in sql

    def test_from_clause(self):
        """Table name appears in FROM."""
        sql, _ = QueryBuilder("cuentas", include_deleted=True).build()
        assert "FROM cuentas" in sql

    def test_where_single_condition(self):
        """Single .where() produces a WHERE clause."""
        sql, params = (
            QueryBuilder("cuentas", include_deleted=True)
            .where("id", 5)
            .build()
        )
        assert "WHERE" in sql
        assert "id = ?" in sql
        assert params == [5]

    def test_where_none_is_ignored(self):
        """Passing None to .where() skips that condition entirely."""
        sql, params = (
            QueryBuilder("cuentas", include_deleted=True)
            .where("id", None)
            .build()
        )
        assert "WHERE" not in sql
        assert params == []

    def test_where_multiple_conditions_joined_with_and(self):
        """Multiple .where() calls are joined with AND."""
        sql, params = (
            QueryBuilder("transacciones", include_deleted=True)
            .where("cuenta_id", 1)
            .where("tipo_movimiento", "egreso")
            .build()
        )
        assert "AND" in sql
        assert params == [1, "egreso"]

    def test_where_operator(self):
        """>= operator is applied correctly."""
        sql, params = (
            QueryBuilder("transacciones", include_deleted=True)
            .where("fecha", "2026-01-01", ">=")
            .build()
        )
        assert "fecha >= ?" in sql
        assert params == ["2026-01-01"]

    def test_invalid_operator_raises(self):
        """An unsupported operator raises ValueError."""
        with pytest.raises(ValueError, match="Operador inválido"):
            QueryBuilder("t", include_deleted=True).where("id", 1, "??")

    def test_where_in_multiple_values(self):
        """where_in produces IN (?, ?, ?) with correct params."""
        sql, params = (
            QueryBuilder("cuotas_credito", include_deleted=True)
            .where_in("estado", ["pendiente", "en_resumen"])
            .build()
        )
        assert "IN (?, ?)" in sql
        assert params == ["pendiente", "en_resumen"]

    def test_where_in_empty_list_ignored(self):
        """Empty list passed to where_in → no WHERE clause."""
        sql, _ = (
            QueryBuilder("cuotas_credito", include_deleted=True)
            .where_in("estado", [])
            .build()
        )
        assert "WHERE" not in sql

    def test_where_between_both_bounds(self):
        """where_between with both values produces BETWEEN."""
        sql, params = (
            QueryBuilder("transacciones", include_deleted=True)
            .where_between("fecha", "2026-01-01", "2026-01-31")
            .build()
        )
        assert "BETWEEN ? AND ?" in sql
        assert params == ["2026-01-01", "2026-01-31"]

    def test_where_between_only_from(self):
        """where_between with only desde → >= condition."""
        sql, params = (
            QueryBuilder("transacciones", include_deleted=True)
            .where_between("fecha", "2026-01-01", None)
            .build()
        )
        assert ">=" in sql
        assert "BETWEEN" not in sql

    def test_where_between_only_to(self):
        """where_between with only hasta → <= condition."""
        sql, params = (
            QueryBuilder("transacciones", include_deleted=True)
            .where_between("fecha", None, "2026-01-31")
            .build()
        )
        assert "<=" in sql
        assert "BETWEEN" not in sql

    def test_order_asc(self):
        """ORDER BY with ASC direction."""
        sql, _ = (
            QueryBuilder("cuentas", include_deleted=True)
            .order("nombre", "ASC")
            .build()
        )
        assert "ORDER BY nombre ASC" in sql

    def test_order_desc(self):
        """ORDER BY with DESC direction."""
        sql, _ = (
            QueryBuilder("transacciones", include_deleted=True)
            .order("fecha", "DESC")
            .build()
        )
        assert "ORDER BY fecha DESC" in sql

    def test_invalid_order_direction_raises(self):
        """An invalid sort direction raises ValueError."""
        with pytest.raises(ValueError, match="Dirección de orden inválida"):
            QueryBuilder("t", include_deleted=True).order("fecha", "RANDOM")

    def test_limit(self):
        """LIMIT appears in SQL."""
        sql, _ = QueryBuilder("cuentas", include_deleted=True).limit(10).build()
        assert "LIMIT 10" in sql

    def test_limit_zero_raises(self):
        """Limit of 0 raises ValueError."""
        with pytest.raises(ValueError, match="límite debe ser positivo"):
            QueryBuilder("t", include_deleted=True).limit(0)

    def test_offset(self):
        """OFFSET appears in SQL."""
        sql, _ = (
            QueryBuilder("cuentas", include_deleted=True)
            .limit(10)
            .offset(20)
            .build()
        )
        assert "OFFSET 20" in sql

    def test_negative_offset_raises(self):
        """Negative offset raises ValueError."""
        with pytest.raises(ValueError, match="offset no puede ser negativo"):
            QueryBuilder("t", include_deleted=True).offset(-1)

    def test_paginar_page_1(self):
        """Page 1 → LIMIT N OFFSET 0."""
        sql, _ = (
            QueryBuilder("t", include_deleted=True)
            .paginar(1, 20)
            .build()
        )
        assert "LIMIT 20" in sql
        assert "OFFSET 0" in sql

    def test_paginar_page_3(self):
        """Page 3 with 20 per page → OFFSET 40."""
        sql, _ = (
            QueryBuilder("t", include_deleted=True)
            .paginar(3, 20)
            .build()
        )
        assert "OFFSET 40" in sql

    def test_paginar_page_zero_raises(self):
        """Page 0 raises ValueError."""
        with pytest.raises(ValueError, match="debe ser >= 1"):
            QueryBuilder("t", include_deleted=True).paginar(0)

    def test_join_appears_in_sql(self):
        """INNER JOIN is assembled correctly."""
        sql, _ = (
            QueryBuilder("transacciones t", include_deleted=True)
            .join("monedas m", "m.id = t.moneda_id")
            .build()
        )
        assert "INNER JOIN monedas m ON m.id = t.moneda_id" in sql

    def test_left_join(self):
        """left_join() produces LEFT JOIN."""
        sql, _ = (
            QueryBuilder("cuentas c", include_deleted=True)
            .left_join("transacciones t", "t.cuenta_id = c.id")
            .build()
        )
        assert "LEFT JOIN" in sql

    def test_invalid_join_type_raises(self):
        """An unsupported JOIN type raises ValueError."""
        with pytest.raises(ValueError, match="Tipo de JOIN inválido"):
            QueryBuilder("t", include_deleted=True).join("other", "other.id = t.id", "DIAGONAL")

    def test_group_by(self):
        """GROUP BY appears in SQL."""
        sql, _ = (
            QueryBuilder("transacciones", include_deleted=True)
            .select("moneda_id", "SUM(monto_minor) AS total")
            .group_by("moneda_id")
            .build()
        )
        assert "GROUP BY moneda_id" in sql

    def test_build_is_idempotent(self):
        """Calling build() twice returns the same SQL both times (no mutation)."""
        builder = (
            QueryBuilder("transacciones")
            .where("cuenta_id", 1)
        )
        sql1, params1 = builder.build()
        sql2, params2 = builder.build()
        assert sql1 == sql2
        assert params1 == params2

    def test_where_raw(self):
        """where_raw injects literal SQL with params."""
        sql, params = (
            QueryBuilder("transacciones", include_deleted=True)
            .where_raw("monto_minor > ?", 5000)
            .build()
        )
        assert "monto_minor > ?" in sql
        assert params == [5000]


class TestQueryBuilderSoftDelete:
    """Tests specifically for the include_deleted / deleted_at IS NULL behavior."""

    def test_soft_delete_filter_injected_by_default(self):
        """Without include_deleted, deleted_at IS NULL is the first WHERE condition."""
        sql, _ = QueryBuilder("transacciones").where("cuenta_id", 1).build()
        assert "deleted_at IS NULL" in sql
        # Must appear BEFORE other conditions
        assert sql.index("deleted_at IS NULL") < sql.index("cuenta_id")

    def test_soft_delete_filter_absent_when_include_deleted(self):
        """include_deleted=True → no deleted_at IS NULL injected."""
        sql, _ = (
            QueryBuilder("transacciones", include_deleted=True)
            .where("cuenta_id", 1)
            .build()
        )
        assert "deleted_at IS NULL" not in sql

    def test_soft_delete_with_no_other_conditions(self):
        """Even with no other filters, deleted_at IS NULL creates a WHERE clause."""
        sql, _ = QueryBuilder("transacciones").build()
        assert "WHERE deleted_at IS NULL" in sql

    def test_build_idempotent_with_soft_delete(self):
        """Soft delete filter must not accumulate across multiple build() calls."""
        builder = QueryBuilder("transacciones").where("cuenta_id", 1)
        sql1, _ = builder.build()
        sql2, _ = builder.build()
        # Count occurrences — must be exactly 1 in each call
        assert sql1.count("deleted_at IS NULL") == 1
        assert sql2.count("deleted_at IS NULL") == 1


class TestQueryBuilderClone:

    def test_clone_is_independent(self):
        """Mutating a clone does not affect the original."""
        original = QueryBuilder("transacciones", include_deleted=True).where("cuenta_id", 1)
        cloned   = original.clone().where("tipo_movimiento", "egreso")

        _, orig_params   = original.build()
        _, cloned_params = cloned.build()

        assert orig_params   == [1]
        assert cloned_params == [1, "egreso"]


class TestQueryBuilderExecution:
    """Tests that actually execute queries against the in-memory DB."""

    def test_ejecutar_returns_rows(self, db, account_ars, cat_supermarket, svc):
        """ejecutar() returns actual rows from the DB."""
        make_expense(svc, account_ars, cat_supermarket)
        rows = (
            QueryBuilder("transacciones")
            .where("cuenta_id", account_ars)
            .ejecutar(db.conn)
        )
        assert len(rows) >= 1

    def test_ejecutar_uno_returns_single_row(self, db, account_ars, cat_supermarket, svc):
        """ejecutar_uno() returns exactly one row or None."""
        result = make_expense(svc, account_ars, cat_supermarket)
        row = (
            QueryBuilder("transacciones", include_deleted=True)
            .where("id", result.transaction_id)
            .ejecutar_uno(db.conn)
        )
        assert row is not None
        assert row["id"] == result.transaction_id

    def test_ejecutar_uno_returns_none_when_not_found(self, db):
        """ejecutar_uno() returns None when no rows match."""
        row = (
            QueryBuilder("transacciones", include_deleted=True)
            .where("id", 99999)
            .ejecutar_uno(db.conn)
        )
        assert row is None

    def test_contar_simple(self, db, account_ars, cat_supermarket, svc):
        """contar() returns the correct count without GROUP BY."""
        for _ in range(4):
            make_expense(svc, account_ars, cat_supermarket)
        total = (
            QueryBuilder("transacciones")
            .where("cuenta_id", account_ars)
            .contar(db.conn)
        )
        assert total == 4

    def test_contar_with_group_by(self, db, account_ars, cat_supermarket, cat_salary, svc):
        """contar() with GROUP BY counts groups, not individual rows."""
        make_expense(svc, account_ars, cat_supermarket)
        svc.create(
            date_str="2026-05-01",
            concept="Salary",
            account_id=account_ars,
            category_id=cat_salary,
            currency_code="ARS",
            amount=100000.0,
            movement_type="ingreso",
        )
        total = (
            QueryBuilder("transacciones")
            .select("tipo_movimiento", "COUNT(*)")
            .group_by("tipo_movimiento")
            .contar(db.conn)
        )
        # Two distinct groups: ingreso and egreso
        assert total == 2

    def test_soft_deleted_rows_excluded_from_list(self, db, account_ars, cat_supermarket, svc):
        """After soft delete, the row is invisible to normal queries."""
        result = make_expense(svc, account_ars, cat_supermarket)
        svc.delete(result.transaction_id)

        rows = (
            QueryBuilder("transacciones")
            .where("id", result.transaction_id)
            .ejecutar(db.conn)
        )
        assert len(rows) == 0

    def test_soft_deleted_rows_visible_with_include_deleted(self, db, account_ars, cat_supermarket, svc):
        """include_deleted=True makes soft-deleted rows visible."""
        result = make_expense(svc, account_ars, cat_supermarket)
        svc.delete(result.transaction_id)

        row = (
            QueryBuilder("transacciones", include_deleted=True)
            .where("id", result.transaction_id)
            .ejecutar_uno(db.conn)
        )
        assert row is not None
        assert row["deleted_at"] is not None


# =============================================================
# TEST: TransactionService — CREATE
# =============================================================

class TestCreate:

    def test_create_expense_success(self, svc, account_ars, cat_supermarket):
        """Standard egreso returns success with a valid ID."""
        result = make_expense(svc, account_ars, cat_supermarket, amount=5000.0)
        assert result.success is True
        assert result.transaction_id > 0

    def test_create_income_success(self, svc, account_ars, cat_salary):
        """Ingreso stores correctly."""
        result = svc.create(
            date_str="2026-05-01",
            concept="May salary",
            account_id=account_ars,
            category_id=cat_salary,
            currency_code="ARS",
            amount=250000.0,
            movement_type="ingreso",
        )
        assert result.success is True
        assert result.data["movement_type"] == "ingreso"

    def test_create_stores_minor_units(self, svc, db, account_ars, cat_supermarket):
        """Amount is stored as minor units (ARS × 100)."""
        result = make_expense(svc, account_ars, cat_supermarket, amount=1234.56)
        row = db.fetchone(
            "SELECT monto_minor FROM transacciones WHERE id = ?;",
            (result.transaction_id,),
        )
        assert row["monto_minor"] == 123456

    def test_create_with_tag_and_notes(self, svc, account_ars, cat_supermarket):
        """Optional tag and notes are persisted in the result data."""
        result = svc.create(
            date_str="2026-05-10",
            concept="Tagged purchase",
            account_id=account_ars,
            category_id=cat_supermarket,
            currency_code="ARS",
            amount=8000.0,
            movement_type="egreso",
            tag="weekly_shopping",
            notes="Included cleaning supplies",
        )
        assert result.data["tag"] == "weekly_shopping"

    def test_create_accepts_date_object(self, svc, account_ars, cat_supermarket):
        """A date object is accepted and converted to string."""
        result = svc.create(
            date_str=date(2026, 5, 10),
            concept="Date object test",
            account_id=account_ars,
            category_id=cat_supermarket,
            currency_code="ARS",
            amount=100.0,
            movement_type="egreso",
        )
        assert result.success is True
        assert result.data["date"] == "2026-05-10"

    def test_create_invalid_date_format_raises(self, svc, account_ars, cat_supermarket):
        """Wrong date format raises ValueError."""
        with pytest.raises(ValueError, match="Invalid date format"):
            svc.create(
                date_str="10/05/2026",
                concept="Bad date",
                account_id=account_ars,
                category_id=cat_supermarket,
                currency_code="ARS",
                amount=100.0,
                movement_type="egreso",
            )

    def test_create_invalid_movement_type_raises(self, svc, account_ars, cat_supermarket):
        """Unrecognized movement type raises ValueError."""
        with pytest.raises(ValueError, match="Invalid movement type"):
            svc.create(
                date_str="2026-05-10",
                concept="Bad type",
                account_id=account_ars,
                category_id=cat_supermarket,
                currency_code="ARS",
                amount=100.0,
                movement_type="gasto",
            )

    def test_create_zero_amount_raises(self, svc, account_ars, cat_supermarket):
        """Zero amount raises ValueError."""
        with pytest.raises(ValueError, match="Amount must be positive"):
            make_expense(svc, account_ars, cat_supermarket, amount=0.0)

    def test_create_negative_amount_raises(self, svc, account_ars, cat_supermarket):
        """Negative amount raises ValueError."""
        with pytest.raises(ValueError, match="Amount must be positive"):
            make_expense(svc, account_ars, cat_supermarket, amount=-100.0)

    def test_create_empty_concept_raises(self, svc, account_ars, cat_supermarket):
        """Whitespace-only concept raises TransactionError."""
        with pytest.raises(TransactionError, match="Concept cannot be empty"):
            svc.create(
                date_str="2026-05-10",
                concept="   ",
                account_id=account_ars,
                category_id=cat_supermarket,
                currency_code="ARS",
                amount=100.0,
                movement_type="egreso",
            )

    def test_create_unknown_currency_raises(self, svc, account_ars, cat_supermarket):
        """Unknown currency code raises CurrencyNotFoundError."""
        with pytest.raises(CurrencyNotFoundError):
            svc.create(
                date_str="2026-05-10",
                concept="Bad currency",
                account_id=account_ars,
                category_id=cat_supermarket,
                currency_code="XYZ",
                amount=100.0,
                movement_type="egreso",
            )

    def test_create_currency_code_case_insensitive(self, svc, account_ars, cat_supermarket):
        """Lowercase currency code 'ars' should be accepted."""
        result = svc.create(
            date_str="2026-05-10",
            concept="Lowercase currency",
            account_id=account_ars,
            category_id=cat_supermarket,
            currency_code="ars",
            amount=100.0,
            movement_type="egreso",
        )
        assert result.success is True

    def test_create_inactive_account_raises(self, svc, db, cat_supermarket):
        """Transaction against an archived account raises AccountNotFoundError."""
        acc_id = db.execute(
            "INSERT INTO cuentas (nombre, tipo, activa) VALUES ('Closed', 'debito', 0);"
        )
        with pytest.raises(AccountNotFoundError):
            svc.create(
                date_str="2026-05-10",
                concept="Archived",
                account_id=acc_id,
                category_id=cat_supermarket,
                currency_code="ARS",
                amount=100.0,
                movement_type="egreso",
            )

    def test_create_unknown_category_raises(self, svc, account_ars):
        """Non-existent category ID raises CategoryNotFoundError."""
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

    def test_create_result_is_dataclass(self, svc, account_ars, cat_supermarket):
        """Result is a TransactionResult dataclass with expected fields."""
        result = make_expense(svc, account_ars, cat_supermarket)
        assert isinstance(result, TransactionResult)
        assert hasattr(result, "success")
        assert hasattr(result, "transaction_id")
        assert hasattr(result, "data")
        assert hasattr(result, "message")


# =============================================================
# TEST: TransactionService — TRANSFER
# =============================================================

class TestTransfer:

    def test_transfer_creates_two_transactions(self, svc, db, account_ars, account_usd, cat_transfer):
        """Transfer produces exactly two transaction rows."""
        before = db.fetchone("SELECT COUNT(*) AS n FROM transacciones;")["n"]
        svc.create_transfer(
            date_str="2026-05-15",
            origin_account_id=account_ars,
            dest_account_id=account_usd,
            currency_code="ARS",
            amount=10000.0,
            category_id=cat_transfer,
        )
        after = db.fetchone("SELECT COUNT(*) AS n FROM transacciones;")["n"]
        assert after - before == 2


    def test_transfer_movement_types(self, svc, db, account_ars, account_usd, cat_transfer):
        """Out leg is egreso, in leg is ingreso."""
        result = svc.create_transfer(
            date_str="2026-05-15",
            origin_account_id=account_ars,
            dest_account_id=account_usd,
            currency_code="ARS",
            amount=2000.0,
            category_id=cat_transfer,
        )
        out = db.fetchone(
            "SELECT tipo_movimiento FROM transacciones WHERE id = ?;",
            (result.data["out_transaction_id"],),
        )
        inn = db.fetchone(
            "SELECT tipo_movimiento FROM transacciones WHERE id = ?;",
            (result.data["in_transaction_id"],),
        )
        assert out["tipo_movimiento"] == "egreso"
        assert inn["tipo_movimiento"] == "ingreso"

    def test_transfer_same_account_raises(self, svc, account_ars, cat_transfer):
        """Same origin and destination raises TransactionError."""
        with pytest.raises(TransactionError, match="must be different"):
            svc.create_transfer(
                date_str="2026-05-15",
                origin_account_id=account_ars,
                dest_account_id=account_ars,
                currency_code="ARS",
                amount=1000.0,
                category_id=cat_transfer,
            )

    def test_transfer_result_contains_both_ids(self, svc, account_ars, account_usd, cat_transfer):
        """Result data contains out_transaction_id and in_transaction_id."""
        result = svc.create_transfer(
            date_str="2026-05-15",
            origin_account_id=account_ars,
            dest_account_id=account_usd,
            currency_code="ARS",
            amount=3000.0,
            category_id=cat_transfer,
        )
        assert "out_transaction_id" in result.data
        assert "in_transaction_id"  in result.data
        assert result.data["out_transaction_id"] != result.data["in_transaction_id"]


# =============================================================
# TEST: TransactionService — READ
# =============================================================

class TestRead:

    def test_get_returns_enriched_row(self, svc, account_ars, cat_supermarket):
        """get() joins account, category, and currency names."""
        result = make_expense(svc, account_ars, cat_supermarket)
        row = svc.get(result.transaction_id)
        assert row is not None
        assert row["account_name"]   == "Galicia ARS"
        assert row["category_name"]  == "Supermercado"
        assert row["currency_code"]  == "ARS"

    def test_get_nonexistent_returns_none(self, svc):
        """get() returns None for a missing ID."""
        assert svc.get(99999) is None

    def test_get_returns_soft_deleted(self, svc, account_ars, cat_supermarket):
        """get() can retrieve a soft-deleted transaction (include_deleted=True)."""
        result = make_expense(svc, account_ars, cat_supermarket)
        svc.delete(result.transaction_id)
        row = svc.get(result.transaction_id)
        assert row is not None
        assert row["deleted_at"] is not None

    def test_list_no_filters_returns_all(self, svc, account_ars, cat_supermarket):
        """list_transactions() with no filters returns all live transactions."""
        for i in range(5):
            make_expense(svc, account_ars, cat_supermarket, amount=100.0 * (i + 1))
        rows = svc.list_transactions()
        assert len(rows) >= 5

    def test_list_excludes_soft_deleted(self, svc, account_ars, cat_supermarket):
        """Soft-deleted transactions do not appear in list_transactions()."""
        result = make_expense(svc, account_ars, cat_supermarket)
        svc.delete(result.transaction_id)
        rows = svc.list_transactions(account_id=account_ars)
        ids = [r["id"] for r in rows]
        assert result.transaction_id not in ids

    def test_list_filter_by_account(self, svc, account_ars, account_usd, cat_supermarket):
        """Filtering by account_id returns only that account's transactions."""
        make_expense(svc, account_ars, cat_supermarket)
        svc.create(
            date_str="2026-05-10",
            concept="USD expense",
            account_id=account_usd,
            category_id=cat_supermarket,
            currency_code="USD",
            amount=50.0,
            movement_type="egreso",
        )
        rows = svc.list_transactions(account_id=account_ars)
        assert all(r["account_name"] == "Galicia ARS" for r in rows)

    def test_list_filter_by_movement_type(self, svc, account_ars, cat_supermarket, cat_salary):
        """Filtering by movement_type returns only matching rows."""
        make_expense(svc, account_ars, cat_supermarket)
        svc.create(
            date_str="2026-05-01",
            concept="Salary",
            account_id=account_ars,
            category_id=cat_salary,
            currency_code="ARS",
            amount=200000.0,
            movement_type="ingreso",
        )
        rows = svc.list_transactions(movement_type="ingreso")
        assert all(r["tipo_movimiento"] == "ingreso" for r in rows)

    def test_list_filter_by_date_range(self, svc, account_ars, cat_supermarket):
        """Date range filter is inclusive on both ends."""
        make_expense(svc, account_ars, cat_supermarket, date_str="2026-04-15", amount=100.0)
        make_expense(svc, account_ars, cat_supermarket, date_str="2026-05-10", amount=200.0)
        make_expense(svc, account_ars, cat_supermarket, date_str="2026-06-01", amount=300.0)

        rows = svc.list_transactions(date_from="2026-05-01", date_to="2026-05-31")
        dates = [r["fecha"] for r in rows]

        assert all("2026-05" in d for d in dates)
        assert "2026-04-15" not in dates
        assert "2026-06-01" not in dates

    def test_list_pagination_no_overlap(self, svc, account_ars, cat_supermarket):
        """Pages do not share rows."""
        for i in range(10):
            make_expense(svc, account_ars, cat_supermarket, amount=float(100 + i))
        page1 = svc.list_transactions(per_page=4, page=1)
        page2 = svc.list_transactions(per_page=4, page=2)
        ids1 = {r["id"] for r in page1}
        ids2 = {r["id"] for r in page2}
        assert ids1.isdisjoint(ids2)

    def test_count_matches_list_total(self, svc, account_ars, cat_supermarket):
        """count_transactions() equals the total rows list_transactions() would return."""
        for _ in range(7):
            make_expense(svc, account_ars, cat_supermarket)
        total = svc.count_transactions(account_id=account_ars, movement_type="egreso")
        rows  = svc.list_transactions(account_id=account_ars, movement_type="egreso", per_page=100)
        assert total == len(rows)

    def test_monthly_summary_totals(self, svc, account_ars, cat_supermarket, cat_salary):
        """monthly_summary correctly sums income, expenses, and net."""
        svc.create(
            date_str="2026-05-01",
            concept="Salary",
            account_id=account_ars,
            category_id=cat_salary,
            currency_code="ARS",
            amount=100000.0,
            movement_type="ingreso",
        )
        make_expense(svc, account_ars, cat_supermarket, amount=30000.0)
        make_expense(svc, account_ars, cat_supermarket, amount=20000.0)

        rows = svc.monthly_summary(month=5, year=2026)
        ars = next((r for r in rows if r["currency_code"] == "ARS"), None)
        assert ars is not None
        assert ars["total_income_minor"]  == 10_000_000
        assert ars["total_expense_minor"] ==  5_000_000
        assert ars["net_minor"]           ==  5_000_000

    def test_monthly_summary_december_year_boundary(self, svc, account_ars, cat_supermarket, cat_salary):
        """December summary does not bleed into January of next year."""
        svc.create(
            date_str="2026-12-15",
            concept="Dec salary",
            account_id=account_ars,
            category_id=cat_salary,
            currency_code="ARS",
            amount=100000.0,
            movement_type="ingreso",
        )
        svc.create(
            date_str="2027-01-05",
            concept="Jan expense",
            account_id=account_ars,
            category_id=cat_supermarket,
            currency_code="ARS",
            amount=5000.0,
            movement_type="egreso",
        )
        rows = svc.monthly_summary(month=12, year=2026)
        ars = next((r for r in rows if r["currency_code"] == "ARS"), None)
        assert ars is not None
        assert ars["total_expense_minor"] == 0   # Jan expense must NOT be included

    def test_monthly_summary_invalid_month_raises(self, svc):
        """Month outside 1–12 raises ValueError."""
        with pytest.raises(ValueError, match="Month must be between"):
            svc.monthly_summary(month=13, year=2026)

    def test_monthly_summary_excludes_soft_deleted(self, svc, account_ars, cat_supermarket):
        """Soft-deleted transactions are excluded from monthly_summary."""
        result = make_expense(svc, account_ars, cat_supermarket, amount=5000.0)
        svc.delete(result.transaction_id)
        rows = svc.monthly_summary(month=5, year=2026)
        ars = next((r for r in rows if r["currency_code"] == "ARS"), None)
        # Either no ARS row at all, or expenses are zero
        if ars:
            assert ars["total_expense_minor"] == 0


# =============================================================
# TEST: TransactionService — UPDATE
# =============================================================

class TestUpdate:

    def test_update_concept(self, svc, db, account_ars, cat_supermarket):
        """Updated concept is persisted."""
        result = make_expense(svc, account_ars, cat_supermarket)
        svc.update(result.transaction_id, concept="New concept")
        row = db.fetchone(
            "SELECT concepto FROM transacciones WHERE id = ?;",
            (result.transaction_id,),
        )
        assert row["concepto"] == "New concept"

    def test_update_amount_and_currency(self, svc, db, account_ars, cat_supermarket):
        """Amount and currency updated together."""
        result = make_expense(svc, account_ars, cat_supermarket, amount=100.0)
        svc.update(result.transaction_id, amount=500.0, currency_code="ARS")
        row = db.fetchone(
            "SELECT monto_minor FROM transacciones WHERE id = ?;",
            (result.transaction_id,),
        )
        assert row["monto_minor"] == 50000

    def test_update_date(self, svc, db, account_ars, cat_supermarket):
        """Updated date is persisted."""
        result = make_expense(svc, account_ars, cat_supermarket, date_str="2026-05-01")
        svc.update(result.transaction_id, date_str="2026-06-15")
        row = db.fetchone(
            "SELECT fecha FROM transacciones WHERE id = ?;",
            (result.transaction_id,),
        )
        assert row["fecha"] == "2026-06-15"

    def test_update_amount_without_currency_raises(self, svc, account_ars, cat_supermarket):
        """Amount without currency_code raises ValueError."""
        result = make_expense(svc, account_ars, cat_supermarket)
        with pytest.raises(ValueError, match="must be updated together"):
            svc.update(result.transaction_id, amount=999.0)

    def test_update_currency_without_amount_raises(self, svc, account_ars, cat_supermarket):
        """currency_code without amount raises ValueError."""
        result = make_expense(svc, account_ars, cat_supermarket)
        with pytest.raises(ValueError, match="must be updated together"):
            svc.update(result.transaction_id, currency_code="USD")

    def test_update_no_fields_returns_failure(self, svc, account_ars, cat_supermarket):
        """Calling update with no fields returns success=False."""
        result = make_expense(svc, account_ars, cat_supermarket)
        update_result = svc.update(result.transaction_id)
        assert update_result.success is False

    def test_update_nonexistent_raises(self, svc):
        """Updating a non-existent transaction raises TransactionError."""
        with pytest.raises(TransactionError, match="not found"):
            svc.update(99999, concept="Ghost")

    def test_update_clears_tag_with_empty_string(self, svc, db, account_ars, cat_supermarket):
        """Passing tag='' stores NULL in the DB."""
        result = svc.create(
            date_str="2026-05-10",
            concept="Tagged",
            account_id=account_ars,
            category_id=cat_supermarket,
            currency_code="ARS",
            amount=100.0,
            movement_type="egreso",
            tag="some_tag",
        )
        svc.update(result.transaction_id, tag="")
        row = db.fetchone(
            "SELECT tag FROM transacciones WHERE id = ?;",
            (result.transaction_id,),
        )
        assert row["tag"] is None

    def test_update_empty_concept_raises(self, svc, account_ars, cat_supermarket):
        """Passing whitespace as concept raises TransactionError."""
        result = make_expense(svc, account_ars, cat_supermarket)
        with pytest.raises(TransactionError, match="Concept cannot be empty"):
            svc.update(result.transaction_id, concept="   ")

    def test_update_invalid_category_raises(self, svc, account_ars, cat_supermarket):
        """Updating to a non-existent category raises CategoryNotFoundError."""
        result = make_expense(svc, account_ars, cat_supermarket)
        with pytest.raises(CategoryNotFoundError):
            svc.update(result.transaction_id, category_id=99999)


# =============================================================
# TEST: TransactionService — DELETE (soft)
# =============================================================

class TestDelete:

    def test_delete_sets_deleted_at(self, svc, db, account_ars, cat_supermarket):
        """After delete, deleted_at is not NULL."""
        result = make_expense(svc, account_ars, cat_supermarket)
        svc.delete(result.transaction_id)
        row = db.fetchone(
            "SELECT deleted_at FROM transacciones WHERE id = ?;",
            (result.transaction_id,),
        )
        assert row["deleted_at"] is not None

    def test_delete_row_still_exists_in_db(self, svc, db, account_ars, cat_supermarket):
        """Soft delete does not physically remove the row."""
        result = make_expense(svc, account_ars, cat_supermarket)
        svc.delete(result.transaction_id)
        row = db.fetchone(
            "SELECT id FROM transacciones WHERE id = ?;",
            (result.transaction_id,),
        )
        assert row is not None

    def test_delete_nonexistent_returns_failure(self, svc):
        """Deleting a non-existent ID returns success=False."""
        result = svc.delete(99999)
        assert result.success is False

    def test_delete_result_carries_id(self, svc, account_ars, cat_supermarket):
        """Result contains the deleted transaction's ID."""
        result = make_expense(svc, account_ars, cat_supermarket)
        delete_result = svc.delete(result.transaction_id)
        assert delete_result.transaction_id == result.transaction_id

    def test_delete_plain_transaction_message_has_no_partner(self, svc, account_ars, cat_supermarket):
        """Non-transfer delete message does not mention partner."""
        result = make_expense(svc, account_ars, cat_supermarket)
        delete_result = svc.delete(result.transaction_id)
        assert "Partner" not in delete_result.message
