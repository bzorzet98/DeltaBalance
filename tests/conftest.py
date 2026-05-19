# tests/conftest.py

import pytest
from pathlib import Path

from db.database import DatabaseManager

from services.transaction_service import TransactionService
from services.debts_service import DebtsService
from services.fees_service import FeesService


# =========================================================
# PATHS
# =========================================================

ROOT_DIR = Path(__file__).resolve().parent.parent

SCHEMA_PATH = ROOT_DIR / "db" / "schema.sql"
SEED_PATH   = ROOT_DIR / "db" / "seed.sql"


# =========================================================
# DATABASE
# =========================================================

@pytest.fixture
def db():
    """
    Fresh in-memory database for every test.

    Loads:
    - schema.sql
    - seed.sql

    This guarantees:
    - currencies exist
    - base categories exist
    - deterministic test state
    """

    manager = DatabaseManager(db_path=":memory:")

    conn = manager.conectar()

    with open(SCHEMA_PATH, encoding="utf-8") as f:
        conn.executescript(f.read())

    with open(SEED_PATH, encoding="utf-8") as f:
        conn.executescript(f.read())

    yield manager

    manager.desconectar()


# =========================================================
# SERVICES
# =========================================================

@pytest.fixture
def transaction_service(db):
    """TransactionService fixture."""
    return TransactionService(db)


@pytest.fixture
def debt_service(db):
    """DebtsService fixture."""
    return DebtsService(db)


@pytest.fixture
def fees_service(db):
    """FeesService fixture."""
    return FeesService(db)


# =========================================================
# ACCOUNTS
# =========================================================

@pytest.fixture
def account_ars(db):
    """
    Active ARS debit account.
    Returns:
        int: account_id
    """

    acc_id = db.execute(
        """
        INSERT INTO cuentas (nombre, tipo)
        VALUES ('Galicia ARS', 'debito');
        """
    )

    ars_id = db.fetchone(
        "SELECT id FROM monedas WHERE codigo = 'ARS';"
    )["id"]

    db.execute(
        """
        INSERT INTO cuentas_saldos
            (cuenta_id, moneda_id, saldo_inicial_minor)
        VALUES (?, ?, 0);
        """,
        (acc_id, ars_id),
    )

    return acc_id


@pytest.fixture
def account_usd(db):
    """
    Active USD debit account.
    Returns:
        int: account_id
    """

    acc_id = db.execute(
        """
        INSERT INTO cuentas (nombre, tipo)
        VALUES ('Galicia USD', 'debito');
        """
    )

    usd_id = db.fetchone(
        "SELECT id FROM monedas WHERE codigo = 'USD';"
    )["id"]

    db.execute(
        """
        INSERT INTO cuentas_saldos
            (cuenta_id, moneda_id, saldo_inicial_minor)
        VALUES (?, ?, 0);
        """,
        (acc_id, usd_id),
    )

    return acc_id


# =========================================================
# CATEGORIES
# =========================================================

@pytest.fixture
def cat_supermarket(db):
    """ID of 'Supermercado' category."""

    row = db.fetchone(
        """
        SELECT id
        FROM categorias
        WHERE subcategoria = 'Supermercado';
        """
    )

    assert row, "Supermercado missing — check seed.sql"

    return row["id"]


@pytest.fixture
def cat_salary(db):
    """ID of 'Sueldo' category."""

    row = db.fetchone(
        """
        SELECT id
        FROM categorias
        WHERE subcategoria = 'Sueldo';
        """
    )

    assert row, "Sueldo missing — check seed.sql"

    return row["id"]


@pytest.fixture
def cat_transfer(db):
    """ID of 'Autotransferencia' category."""

    row = db.fetchone(
        """
        SELECT id
        FROM categorias
        WHERE subcategoria = 'Autotransferencia';
        """
    )

    assert row, "Autotransferencia missing — check seed.sql"

    return row["id"]



@pytest.fixture
def credit_account(db):
    """Active credit account. Returns its ID."""
    acc_id = db.execute(
        "INSERT INTO cuentas (nombre, tipo) VALUES ('Visa Galicia', 'credito');"
    )
    ars_id = db.fetchone("SELECT id FROM monedas WHERE codigo = 'ARS';")["id"]
    db.execute(
        "INSERT INTO cuentas_saldos (cuenta_id, moneda_id, saldo_inicial_minor) VALUES (?, ?, 0);",
        (acc_id, ars_id),
    )
    return acc_id


@pytest.fixture
def category_hogar(db):
    """Returns ID of 'Hogar: Mantenimiento' category from seed."""
    row = db.fetchone(
        "SELECT id FROM categorias WHERE subcategoria = 'Hogar: Mantenimiento';"
    )
    assert row, "Hogar: Mantenimiento missing — check seed.sql"
    return row["id"]
