"""
DeltaBalance — tests/test_debts_service.py

Full test suite for services/debts_service.py.

Coverage:
    - Create debts (a_favor / en_contra, validations, minor units)
    - Read (get, list, list_active, get_payments, summary_by_person, count)
    - Register payments (partial, full, auto-settle, payment types, edge cases)
    - Update (fields, settled guard, empty guard)
    - Write off (active → incobrable, settled guard)

Run:
    pytest tests/test_debts_service.py -v
    pytest tests/test_debts_service.py -v -k "TestPayment"
"""

import pytest
from pathlib import Path

from db.database import to_minor
from services.debts_service import (
    DebtResult,
    DebtError,
    DebtNotFoundError,
    DebtAlreadySettledError,
    PaymentExceedsBalanceError,
)
from tests.helpers import make_debt, make_payment

# =============================================================
# PATHS
# =============================================================
SCHEMA_PATH = Path(__file__).resolve().parent.parent / "db" / "schema.sql"
SEED_PATH   = Path(__file__).resolve().parent.parent / "db" / "seed.sql"

# =============================================================
# TEST: CREATE
# =============================================================

class TestCreate:

    def test_create_a_favor_success(self, debt_service):
        """Creating an 'a_favor' debt returns success with a valid ID."""
        result = make_debt(debt_service, debt_type="a_favor")
        assert result.success is True
        assert result.debt_id is not None
        assert result.debt_id > 0

    def test_create_en_contra_success(self, debt_service):
        """Creating an 'en_contra' debt returns success."""
        result = make_debt(debt_service, debt_type="en_contra")
        assert result.success is True
        assert result.data["type"] == "en_contra"

    def test_create_stores_minor_units(self, debt_service, db):
        """Amount is stored as minor units (ARS × 100)."""
        result = make_debt(debt_service, amount=1234.56)
        row = db.fetchone(
            "SELECT monto_original_minor, monto_pendiente_minor FROM deudas WHERE id = ?;",
            (result.debt_id,),
        )
        assert row["monto_original_minor"]  == 123456
        assert row["monto_pendiente_minor"] == 123456

    def test_create_original_equals_pending_on_creation(self, debt_service, db):
        """monto_original_minor and monto_pendiente_minor start equal."""
        result = make_debt(debt_service, amount=3000.0)
        row = db.fetchone("SELECT * FROM deudas WHERE id = ?;", (result.debt_id,))
        assert row["monto_original_minor"] == row["monto_pendiente_minor"]

    def test_create_initial_estado_is_activa(self, debt_service, db):
        """Newly created debt starts with estado='activa'."""
        result = make_debt(debt_service)
        row = db.fetchone("SELECT estado FROM deudas WHERE id = ?;", (result.debt_id,))
        assert row["estado"] == "activa"

    def test_create_with_due_date(self, debt_service, db):
        """Due date is stored correctly."""
        result = debt_service.create(
            person="Papi",
            debt_type="en_contra",
            amount=10000.0,
            currency_code="ARS",
            date_str="2026-05-01",
            concept="Loan",
            due_date="2026-08-01",
        )
        row = db.fetchone(
            "SELECT fecha_vencimiento FROM deudas WHERE id = ?;", (result.debt_id,)
        )
        assert row["fecha_vencimiento"] == "2026-08-01"

    def test_create_with_origen_tipo_and_id(self, debt_service, db):
        """origen_tipo and origen_id are stored for traceability."""
        result = debt_service.create(
            person="Noe",
            debt_type="a_favor",
            amount=2000.0,
            currency_code="ARS",
            date_str="2026-05-10",
            concept="Split lunch",
            origen_tipo="transaccion",
            origen_id=42,
        )
        row = db.fetchone(
            "SELECT origen_tipo, origen_id FROM deudas WHERE id = ?;", (result.debt_id,)
        )
        assert row["origen_tipo"] == "transaccion"
        assert row["origen_id"]   == 42

    def test_create_usd_debt(self, debt_service, db):
        """USD debt is stored with correct currency reference."""
        result = debt_service.create(
            person="Ana",
            debt_type="a_favor",
            amount=100.0,
            currency_code="USD",
            date_str="2026-05-10",
            concept="USD loan",
        )
        row = db.fetchone(
            """
            SELECT m.codigo FROM deudas d
            JOIN monedas m ON m.id = d.moneda_id
            WHERE d.id = ?;
            """,
            (result.debt_id,),
        )
        assert row["codigo"] == "USD"

    def test_create_zero_amount_raises(self, debt_service):
        """Zero amount raises ValueError."""
        with pytest.raises(ValueError, match="Amount must be positive"):
            make_debt(debt_service, amount=0.0)

    def test_create_negative_amount_raises(self, debt_service):
        """Negative amount raises ValueError."""
        with pytest.raises(ValueError, match="Amount must be positive"):
            make_debt(debt_service, amount=-500.0)

    def test_create_empty_person_raises(self, debt_service):
        """Empty person name raises DebtError."""
        with pytest.raises(DebtError, match="Person name cannot be empty"):
            debt_service.create(
                person="   ",
                debt_type="a_favor",
                amount=1000.0,
                currency_code="ARS",
                date_str="2026-05-10",
                concept="Test",
            )

    def test_create_empty_concept_raises(self, debt_service):
        """Empty concept raises DebtError."""
        with pytest.raises(DebtError, match="Concept cannot be empty"):
            debt_service.create(
                person="Noe",
                debt_type="a_favor",
                amount=1000.0,
                currency_code="ARS",
                date_str="2026-05-10",
                concept="   ",
            )

    def test_create_invalid_debt_type_raises(self, debt_service):
        """Invalid debt type raises ValueError."""
        with pytest.raises(ValueError, match="Invalid debt type"):
            make_debt(debt_service, debt_type="neutral")

    def test_create_invalid_date_raises(self, debt_service):
        """Invalid date format raises ValueError."""
        with pytest.raises(ValueError, match="Invalid date format"):
            make_debt(debt_service, date_str="10-05-2026")

    def test_create_unknown_currency_raises(self, debt_service):
        """Unknown currency raises ValueError."""
        with pytest.raises(ValueError, match="not found"):
            make_debt(debt_service, currency_code="XYZ")

    def test_create_result_is_dataclass(self, debt_service):
        """Result is a DebtResult dataclass with expected fields."""
        result = make_debt(debt_service)
        assert isinstance(result, DebtResult)
        assert hasattr(result, "success")
        assert hasattr(result, "debt_id")
        assert hasattr(result, "data")
        assert hasattr(result, "message")


# =============================================================
# TEST: READ
# =============================================================

class TestRead:

    def test_get_returns_enriched_row(self, debt_service):
        """get() returns a row with currency_code and decimales via JOIN."""
        result = make_debt(debt_service, amount=3000.0)
        row = debt_service.get(result.debt_id)
        assert row is not None
        assert row["currency_code"] == "ARS"
        assert row["decimales"]     == 2

    def test_get_nonexistent_returns_none(self, debt_service):
        """get() returns None for a non-existent ID."""
        assert debt_service.get(99999) is None

    def test_list_returns_all_when_no_filters(self, debt_service):
        """list() with no filters returns all debts."""
        for i in range(5):
            make_debt(debt_service, person=f"Person{i}", amount=1000.0 * (i + 1))
        rows = debt_service.list_debts()
        assert len(rows) >= 5

    def test_list_filters_by_person(self, debt_service):
        """list(person=X) returns only that person's debts."""
        make_debt(debt_service, person="Noe", amount=1000.0)
        make_debt(debt_service, person="Papi", amount=2000.0)
        rows = debt_service.list_debts(person="Noe")
        assert all(r["entidad_persona"] == "Noe" for r in rows)

    def test_list_filters_by_type(self, debt_service):
        """list(debt_type='a_favor') returns only a_favor debts."""
        make_debt(debt_service, debt_type="a_favor")
        make_debt(debt_service, debt_type="en_contra")
        rows = debt_service.list_debts(debt_type="a_favor")
        assert all(r["tipo"] == "a_favor" for r in rows)

    def test_list_filters_by_estado(self, debt_service):
        """list(estado='activa') returns only active debts."""
        result = make_debt(debt_service, amount=1000.0)
        make_payment(debt_service, result.debt_id, amount=1000.0)  # settles it
        rows = debt_service.list_debts(estado="activa")
        ids = [r["id"] for r in rows]
        assert result.debt_id not in ids

    def test_list_active_shortcut(self, debt_service):
        """list_active() is equivalent to list(estado='activa')."""
        make_debt(debt_service, amount=5000.0)
        rows = debt_service.list_active()
        assert all(r["estado"] == "activa" for r in rows)

    def test_list_active_filters_by_type(self, debt_service):
        """list_active(debt_type='en_contra') returns only active en_contra debts."""
        make_debt(debt_service, debt_type="a_favor")
        make_debt(debt_service, debt_type="en_contra")
        rows = debt_service.list_active(debt_type="en_contra")
        assert all(r["tipo"] == "en_contra" for r in rows)

    def test_list_pagination_no_overlap(self, debt_service):
        """Pages do not share rows."""
        for i in range(10):
            make_debt(debt_service, person=f"P{i}", amount=1000.0)
        page1 = debt_service.list_debts(page=1, per_page=4)
        page2 = debt_service.list_debts(page=2, per_page=4)
        ids1 = {r["id"] for r in page1}
        ids2 = {r["id"] for r in page2}
        assert ids1.isdisjoint(ids2)

    def test_count_matches_list_total(self, debt_service):
        """count() equals the total rows list() would return."""
        for i in range(6):
            make_debt(debt_service, person="Noe", amount=1000.0)
        total = debt_service.count_debts(person="Noe")
        rows  = debt_service.list_debts(person="Noe", per_page=100)
        assert total == len(rows)

    def test_get_payments_empty_on_new_debt(self, debt_service):
        """A new debt has no payment records."""
        result = make_debt(debt_service)
        payments = debt_service.get_payments(result.debt_id)
        assert payments == []

    def test_get_payments_returns_all_records(self, debt_service):
        """get_payments returns one row per payment registered."""
        result = make_debt(debt_service, amount=6000.0)
        make_payment(debt_service, result.debt_id, amount=2000.0)
        make_payment(debt_service, result.debt_id, amount=2000.0)
        payments = debt_service.get_payments(result.debt_id)
        assert len(payments) == 2

    def test_get_payments_nonexistent_debt_raises(self, debt_service):
        """get_payments on a non-existent debt raises DebtNotFoundError."""
        with pytest.raises(DebtNotFoundError):
            debt_service.get_payments(99999)

    def test_summary_by_person_groups_correctly(self, debt_service):
        """summary_by_person groups totals by person and type."""
        make_debt(debt_service, person="Noe", debt_type="a_favor",   amount=3000.0)
        make_debt(debt_service, person="Noe", debt_type="a_favor",   amount=2000.0)
        make_debt(debt_service, person="Papi", debt_type="en_contra", amount=8000.0)

        rows = debt_service.summary_by_person(currency_code="ARS")
        noe_row  = next((r for r in rows if r["person"] == "Noe"  and r["tipo"] == "a_favor"), None)
        papi_row = next((r for r in rows if r["person"] == "Papi" and r["tipo"] == "en_contra"), None)

        assert noe_row  is not None
        assert papi_row is not None
        assert noe_row["total_pending_minor"]  == to_minor(5000.0)
        assert papi_row["total_pending_minor"] == to_minor(8000.0)

    def test_summary_excludes_settled_debts(self, debt_service):
        """summary_by_person does not include saldada debts."""
        result = make_debt(debt_service, person="Noe", amount=1000.0)
        make_payment(debt_service, result.debt_id, amount=1000.0)  # settle it

        rows = debt_service.summary_by_person(currency_code="ARS")
        noe_rows = [r for r in rows if r["person"] == "Noe"]
        assert all(r["total_pending_minor"] == 0 for r in noe_rows) or len(noe_rows) == 0


# =============================================================
# TEST: REGISTER PAYMENT
# =============================================================

class TestPayment:

    def test_partial_payment_reduces_pending(self, debt_service, db):
        """A partial payment reduces monto_pendiente_minor correctly."""
        result = make_debt(debt_service, amount=5000.0)
        make_payment(debt_service, result.debt_id, amount=2000.0)

        row = db.fetchone(
            "SELECT monto_pendiente_minor FROM deudas WHERE id = ?;", (result.debt_id,)
        )
        assert row["monto_pendiente_minor"] == to_minor(3000.0)

    def test_partial_payment_keeps_estado_activa(self, debt_service, db):
        """Partial payment leaves estado as 'activa'."""
        result = make_debt(debt_service, amount=5000.0)
        make_payment(debt_service, result.debt_id, amount=2000.0)

        row = db.fetchone("SELECT estado FROM deudas WHERE id = ?;", (result.debt_id,))
        assert row["estado"] == "activa"

    def test_full_payment_settles_debt(self, debt_service, db):
        """Paying the full amount transitions estado to 'saldada'."""
        result = make_debt(debt_service, amount=5000.0)
        payment = make_payment(debt_service, result.debt_id, amount=5000.0)

        assert payment.data["settled"] is True
        row = db.fetchone("SELECT estado FROM deudas WHERE id = ?;", (result.debt_id,))
        assert row["estado"] == "saldada"

    def test_full_payment_pending_reaches_zero(self, debt_service, db):
        """After full payment, monto_pendiente_minor is exactly 0."""
        result = make_debt(debt_service, amount=5000.0)
        make_payment(debt_service, result.debt_id, amount=5000.0)

        row = db.fetchone(
            "SELECT monto_pendiente_minor FROM deudas WHERE id = ?;", (result.debt_id,)
        )
        assert row["monto_pendiente_minor"] == 0

    def test_multiple_partial_payments(self, debt_service, db):
        """Multiple partial payments accumulate correctly."""
        result = make_debt(debt_service, amount=9000.0)
        make_payment(debt_service, result.debt_id, amount=3000.0)
        make_payment(debt_service, result.debt_id, amount=3000.0)
        make_payment(debt_service, result.debt_id, amount=3000.0)

        row = db.fetchone(
            "SELECT monto_pendiente_minor, estado FROM deudas WHERE id = ?;", (result.debt_id,)
        )
        assert row["monto_pendiente_minor"] == 0
        assert row["estado"] == "saldada"

    def test_payment_creates_deuda_pagos_row(self, debt_service, db):
        """Each payment creates a row in deuda_pagos."""
        result = make_debt(debt_service, amount=5000.0)
        make_payment(debt_service, result.debt_id, amount=1000.0)
        make_payment(debt_service, result.debt_id, amount=2000.0)

        rows = db.fetchall(
            "SELECT * FROM deuda_pagos WHERE deuda_id = ?;", (result.debt_id,)
        )
        assert len(rows) == 2

    def test_payment_stores_correct_minor_units(self, debt_service, db):
        """Payment minor units are stored correctly."""
        result = make_debt(debt_service, amount=5000.0)
        make_payment(debt_service, result.debt_id, amount=1234.56)

        row = db.fetchone(
            "SELECT monto_applied_minor FROM deuda_pagos WHERE deuda_id = ?;",
            (result.debt_id,),
        )
        assert row["monto_applied_minor"] == 123456

    def test_payment_with_transaction_id(
                                            self,
                                            debt_service,
                                            transaction_service,
                                            db,
                                        ):
        """transaction_id is stored in deuda_pagos when provided."""

        result = make_debt(debt_service, amount=5000.0)

        tx_result = transaction_service.create(
            date_str="2026-05-01",
            concept="Payment Transaction",
            account_id=1,
            category_id=1,
            currency_code="ARS",
            amount=1000,
            movement_type="egreso",
        )

        debt_service.register_payment(
            debt_id=result.debt_id,
            amount=1000.0,
            currency_code="ARS",
            date_str="2026-05-15",
            transaction_id=tx_result.transaction_id,
        )

        row = db.fetchone(
            "SELECT transaccion_id FROM deuda_pagos WHERE deuda_id = ?;",
            (result.debt_id,),
        )

        assert row["transaccion_id"] == tx_result.transaction_id
    def test_payment_type_compensacion(self, debt_service, db):
        """Payment type 'compensacion' is stored correctly."""
        result = make_debt(debt_service, amount=5000.0)
        make_payment(debt_service, result.debt_id, amount=1000.0, payment_type="compensacion")

        row = db.fetchone(
            "SELECT tipo_pago FROM deuda_pagos WHERE deuda_id = ?;", (result.debt_id,)
        )
        assert row["tipo_pago"] == "compensacion"

    def test_payment_result_has_pending_amount(self, debt_service):
        """Payment result data includes pending_amount as a float."""
        result  = make_debt(debt_service, amount=5000.0)
        payment = make_payment(debt_service, result.debt_id, amount=2000.0)
        assert payment.data["pending_amount"] == pytest.approx(3000.0, abs=0.01)

    def test_payment_exceeds_balance_raises(self, debt_service):
        """Paying more than the pending balance raises PaymentExceedsBalanceError."""
        result = make_debt(debt_service, amount=1000.0)
        with pytest.raises(PaymentExceedsBalanceError):
            make_payment(debt_service, result.debt_id, amount=1500.0)

    def test_payment_on_settled_debt_raises(self, debt_service):
        """Paying a settled debt raises DebtAlreadySettledError."""
        result = make_debt(debt_service, amount=1000.0)
        make_payment(debt_service, result.debt_id, amount=1000.0)  # settle
        with pytest.raises(DebtAlreadySettledError):
            make_payment(debt_service, result.debt_id, amount=500.0)

    def test_payment_on_incobrable_debt_raises(self, debt_service):
        """Paying a written-off debt raises DebtAlreadySettledError."""
        result = make_debt(debt_service, amount=1000.0)
        debt_service.write_off(result.debt_id)
        with pytest.raises(DebtAlreadySettledError):
            make_payment(debt_service, result.debt_id, amount=500.0)

    def test_payment_zero_amount_raises(self, debt_service):
        """Zero payment raises ValueError."""
        result = make_debt(debt_service, amount=1000.0)
        with pytest.raises(ValueError, match="Payment amount must be positive"):
            make_payment(debt_service, result.debt_id, amount=0.0)

    def test_payment_nonexistent_debt_raises(self, debt_service):
        """Paying a non-existent debt raises DebtNotFoundError."""
        with pytest.raises(DebtNotFoundError):
            make_payment(debt_service, debt_id=99999, amount=100.0)

    def test_payment_invalid_type_raises(self, debt_service):
        """Invalid payment_type raises ValueError."""
        result = make_debt(debt_service, amount=1000.0)
        with pytest.raises(ValueError, match="Invalid payment_type"):
            debt_service.register_payment(
                debt_id=result.debt_id,
                amount=100.0,
                currency_code="ARS",
                date_str="2026-05-15",
                payment_type="cash_under_table",
            )

    def test_settled_message_in_result(self, debt_service):
        """Full payment result message mentions 'settled'."""
        result  = make_debt(debt_service, amount=1000.0)
        payment = make_payment(debt_service, result.debt_id, amount=1000.0)
        assert "settled" in payment.message.lower()

    def test_partial_message_shows_remaining(self, debt_service):
        """Partial payment result message shows remaining amount."""
        result  = make_debt(debt_service, amount=5000.0)
        payment = make_payment(debt_service, result.debt_id, amount=2000.0)
        assert "Remaining" in payment.message or "remaining" in payment.message


# =============================================================
# TEST: UPDATE
# =============================================================

class TestUpdate:

    def test_update_person(self, debt_service, db):
        """Updated person name is persisted."""
        result = make_debt(debt_service, person="Noe")
        debt_service.update(result.debt_id, person="Noe Updated")
        row = db.fetchone(
            "SELECT entidad_persona FROM deudas WHERE id = ?;", (result.debt_id,)
        )
        assert row["entidad_persona"] == "Noe Updated"

    def test_update_due_date(self, debt_service, db):
        """Updated due date is persisted."""
        result = make_debt(debt_service)
        debt_service.update(result.debt_id, due_date="2026-12-31")
        row = db.fetchone(
            "SELECT fecha_vencimiento FROM deudas WHERE id = ?;", (result.debt_id,)
        )
        assert row["fecha_vencimiento"] == "2026-12-31"

    def test_update_clear_due_date(self, debt_service, db):
        """Passing due_date='' clears the due date (stores NULL)."""
        result = debt_service.create(
            person="Noe", debt_type="a_favor", amount=1000.0,
            currency_code="ARS", date_str="2026-05-01", concept="Test",
            due_date="2026-12-31",
        )
        debt_service.update(result.debt_id, due_date="")
        row = db.fetchone(
            "SELECT fecha_vencimiento FROM deudas WHERE id = ?;", (result.debt_id,)
        )
        assert row["fecha_vencimiento"] is None

    def test_update_no_fields_returns_failure(self, debt_service):
        """Calling update with no fields returns success=False."""
        result = make_debt(debt_service)
        update_result = debt_service.update(result.debt_id)
        assert update_result.success is False

    def test_update_nonexistent_raises(self, debt_service):
        """Updating a non-existent debt raises DebtNotFoundError."""
        with pytest.raises(DebtNotFoundError):
            debt_service.update(99999, person="Ghost")

    def test_update_settled_debt_raises(self, debt_service):
        """Updating a settled debt raises DebtAlreadySettledError."""
        result = make_debt(debt_service, amount=1000.0)
        make_payment(debt_service, result.debt_id, amount=1000.0)
        with pytest.raises(DebtAlreadySettledError):
            debt_service.update(result.debt_id, person="New Name")

    def test_update_empty_person_raises(self, debt_service):
        """Updating with empty person name raises DebtError."""
        result = make_debt(debt_service)
        with pytest.raises(DebtError, match="Person name cannot be empty"):
            debt_service.update(result.debt_id, person="   ")


# =============================================================
# TEST: WRITE OFF
# =============================================================

class TestWriteOff:

    def test_write_off_sets_incobrable(self, debt_service, db):
        """write_off transitions estado to 'incobrable'."""
        result = make_debt(debt_service)
        debt_service.write_off(result.debt_id, notes="Never got it back")
        row = db.fetchone("SELECT estado FROM deudas WHERE id = ?;", (result.debt_id,))
        assert row["estado"] == "incobrable"

    def test_write_off_stores_notes(self, debt_service, db):
        """Notes passed to write_off are stored in the DB."""
        result = make_debt(debt_service)
        debt_service.write_off(result.debt_id, notes="Forgiven")
        row = db.fetchone("SELECT notas FROM deudas WHERE id = ?;", (result.debt_id,))
        assert row["notas"] == "Forgiven"

    def test_write_off_already_settled_raises(self, debt_service):
        """Writing off a settled debt raises DebtAlreadySettledError."""
        result = make_debt(debt_service, amount=1000.0)
        make_payment(debt_service, result.debt_id, amount=1000.0)
        with pytest.raises(DebtAlreadySettledError):
            debt_service.write_off(result.debt_id)

    def test_write_off_already_incobrable_raises(self, debt_service):
        """Writing off an already written-off debt raises DebtAlreadySettledError."""
        result = make_debt(debt_service)
        debt_service.write_off(result.debt_id)
        with pytest.raises(DebtAlreadySettledError):
            debt_service.write_off(result.debt_id)

    def test_write_off_nonexistent_raises(self, debt_service):
        """Writing off a non-existent debt raises DebtNotFoundError."""
        with pytest.raises(DebtNotFoundError):
            debt_service.write_off(99999)

    def test_write_off_result_success(self, debt_service):
        """write_off returns a successful DebtResult."""
        result = make_debt(debt_service)
        wo = debt_service.write_off(result.debt_id)
        assert wo.success is True
        assert wo.debt_id == result.debt_id