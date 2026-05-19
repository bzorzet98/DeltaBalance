"""
DeltaBalance — tests/test_fees_service.py

Full test suite for services/fees_service.py.

Coverage:
    - create_purchase (fee generation, minor units, validations, year wraparound)
    - Read purchases (get, list, get_fees_for_purchase)
    - Fee projection (fees_by_month, fees_projection)
    - Statement lifecycle (open → confirm_fee → close → pay)
    - Edge cases (idempotent open, already paid guards, fee already confirmed)
    - cancel_purchase

Run:
    pytest tests/test_fees_service.py -v
    pytest tests/test_fees_service.py -v -k "TestStatement"
"""

import pytest
from pathlib import Path
from datetime import datetime

from db.database import DatabaseManager, to_minor, from_minor
from services.fees_service import (
    FeesService,
    FeesResult,
    FeesError,
    PurchaseNotFoundError,
    StatementNotFoundError,
    FeeNotFoundError,
    StatementAlreadyPaidError,
    FeeAlreadyConfirmedError,
)

from tests.helpers import make_purchase, full_statement_cycle

# =============================================================
# PATHS
# =============================================================

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "db" / "schema.sql"
SEED_PATH   = Path(__file__).resolve().parent.parent / "db" / "seed.sql"

# =============================================================
# TEST: CREATE PURCHASE
# =============================================================

class TestCreatePurchase:

    def test_create_purchase_success(self, fees_service, credit_account, category_hogar):
        """create_purchase returns success with a valid entity_id."""
        result = make_purchase(fees_service, credit_account, category_hogar)
        assert result.success is True
        assert result.entity_id is not None
        assert result.entity_id > 0

    def test_create_purchase_generates_correct_fee_count(self, fees_service, db, credit_account, category_hogar):
        """N fees are generated for a purchase of N installments."""
        result = make_purchase(fees_service, credit_account, category_hogar, total_fees=12)
        rows = db.fetchall(
            "SELECT * FROM cuotas_credito WHERE compra_id = ?;", (result.entity_id,)
        )
        assert len(rows) == 12

    def test_create_purchase_fees_start_pending(self, fees_service, db, credit_account, category_hogar):
        """All generated fees start with estado='pendiente'."""
        result = make_purchase(fees_service, credit_account, category_hogar, total_fees=6)
        rows = db.fetchall(
            "SELECT estado FROM cuotas_credito WHERE compra_id = ?;", (result.entity_id,)
        )
        assert all(r["estado"] == "pendiente" for r in rows)

    def test_create_purchase_fees_numbered_sequentially(self, fees_service, db, credit_account, category_hogar):
        """Fees are numbered 1 through N sequentially."""
        result = make_purchase(fees_service, credit_account, category_hogar, total_fees=6)
        rows = db.fetchall(
            "SELECT numero_cuota FROM cuotas_credito WHERE compra_id = ? ORDER BY numero_cuota;",
            (result.entity_id,),
        )
        numbers = [r["numero_cuota"] for r in rows]
        assert numbers == list(range(1, 7))

    def test_create_purchase_fees_projected_months(self, fees_service, db, credit_account, category_hogar):
        """Fees are projected to consecutive months starting from purchase month."""
        result = make_purchase(fees_service, credit_account, category_hogar,
                               date_str="2026-05-01", total_fees=4)
        rows = db.fetchall(
            """
            SELECT mes_proyectado, anio_proyectado FROM cuotas_credito
            WHERE compra_id = ? ORDER BY numero_cuota;
            """,
            (result.entity_id,),
        )
        expected = [(5, 2026), (6, 2026), (7, 2026), (8, 2026)]
        actual   = [(r["mes_proyectado"], r["anio_proyectado"]) for r in rows]
        assert actual == expected

    def test_create_purchase_fees_wrap_december_to_january(self, fees_service, db, credit_account, category_hogar):
        """Fees that cross December wrap correctly to January of the next year."""
        result = make_purchase(fees_service, credit_account, category_hogar,
                               date_str="2026-11-01", total_fees=4)
        rows = db.fetchall(
            """
            SELECT mes_proyectado, anio_proyectado FROM cuotas_credito
            WHERE compra_id = ? ORDER BY numero_cuota;
            """,
            (result.entity_id,),
        )
        expected = [(11, 2026), (12, 2026), (1, 2027), (2, 2027)]
        actual   = [(r["mes_proyectado"], r["anio_proyectado"]) for r in rows]
        assert actual == expected

    def test_create_purchase_stores_minor_units(self, fees_service, db, credit_account, category_hogar):
        """Total amount and per-fee amount are stored as minor units."""
        result = make_purchase(fees_service, credit_account, category_hogar,
                               total_amount=120000.0, total_fees=12)
        row = db.fetchone(
            "SELECT monto_total_minor, monto_por_cuota_minor FROM compras_cuotas WHERE id = ?;",
            (result.entity_id,),
        )
        assert row["monto_total_minor"]     == to_minor(120000.0)
        assert row["monto_por_cuota_minor"] == to_minor(10000.0)

    def test_create_purchase_custom_per_fee_amount(self, fees_service, db, credit_account, category_hogar):
        """Custom amount_per_fee overrides the calculated value."""
        result = fees_service.create_purchase(
            date_str="2026-05-01",
            concept="Phone with interest",
            account_id=credit_account,
            category_id=category_hogar,
            currency_code="ARS",
            total_amount=100000.0,
            total_fees=12,
            amount_per_fee=9500.0,   # with CFT
        )
        row = db.fetchone(
            "SELECT monto_por_cuota_minor FROM compras_cuotas WHERE id = ?;",
            (result.entity_id,),
        )
        assert row["monto_por_cuota_minor"] == to_minor(9500.0)

    def test_create_purchase_single_installment(self, fees_service, db, credit_account, category_hogar):
        """A single-installment purchase generates exactly 1 fee."""
        result = make_purchase(fees_service, credit_account, category_hogar, total_fees=1)
        rows = db.fetchall(
            "SELECT * FROM cuotas_credito WHERE compra_id = ?;", (result.entity_id,)
        )
        assert len(rows) == 1

    def test_create_purchase_zero_amount_raises(self, fees_service, credit_account, category_hogar):
        """Zero total amount raises FeesError."""
        with pytest.raises(FeesError, match="Total amount must be positive"):
            make_purchase(fees_service, credit_account, category_hogar, total_amount=0.0)

    def test_create_purchase_zero_fees_raises(self, fees_service, credit_account, category_hogar):
        """Zero installments raises FeesError."""
        with pytest.raises(FeesError, match="Total fees must be >= 1"):
            make_purchase(fees_service, credit_account, category_hogar, total_fees=0)

    def test_create_purchase_empty_concept_raises(self, fees_service, credit_account, category_hogar):
        """Empty concept raises FeesError."""
        with pytest.raises(FeesError, match="Concept cannot be empty"):
            make_purchase(fees_service, credit_account, category_hogar, concept="   ")

    def test_create_purchase_invalid_account_raises(self, fees_service, category_hogar):
        """Non-existent account raises FeesError."""
        with pytest.raises(FeesError, match="not found or is inactive"):
            make_purchase(fees_service, credit_account=99999, category_hogar=category_hogar)

    def test_create_purchase_invalid_category_raises(self, fees_service, credit_account):
        """Non-existent category raises FeesError."""
        with pytest.raises(FeesError, match="not found"):
            make_purchase(fees_service, credit_account=credit_account, category_hogar=99999)

    def test_create_purchase_unknown_currency_raises(self, fees_service, credit_account, category_hogar):
        """Unknown currency raises ValueError."""
        with pytest.raises(ValueError, match="not found"):
            make_purchase(fees_service, credit_account, category_hogar, currency_code="XYZ")

    def test_create_purchase_result_is_dataclass(self, fees_service, credit_account, category_hogar):
        """Result is a FeesResult dataclass."""
        result = make_purchase(fees_service, credit_account, category_hogar)
        assert isinstance(result, FeesResult)
        assert hasattr(result, "success")
        assert hasattr(result, "entity_id")
        assert hasattr(result, "data")
        assert hasattr(result, "message")


# =============================================================
# TEST: READ PURCHASES
# =============================================================

class TestReadPurchases:

    def test_get_purchase_returns_enriched_row(self, fees_service, credit_account, category_hogar):
        """get_purchase returns account_name, category_name, currency_code."""
        result = make_purchase(fees_service, credit_account, category_hogar)
        row = fees_service.get_purchase(result.entity_id)
        assert row is not None
        assert row["account_name"]   == "Visa Galicia"
        assert row["category_name"]  == "Hogar: Mantenimiento"
        assert row["currency_code"]  == "ARS"

    def test_get_purchase_nonexistent_returns_none(self, fees_service):
        """get_purchase returns None for a non-existent ID."""
        assert fees_service.get_purchase(99999) is None

    def test_list_purchases_returns_all(self, fees_service, credit_account, category_hogar):
        """list_purchases with no filters returns all purchases."""
        for i in range(4):
            make_purchase(fees_service, credit_account, category_hogar,
                          concept=f"Purchase {i}", total_fees=i + 1)
        rows = fees_service.list_purchases()
        assert len(rows) >= 4

    def test_list_purchases_filters_by_account(self, fees_service, db, credit_account, category_hogar):
        """list_purchases(account_id=X) returns only that account's purchases."""
        make_purchase(fees_service, credit_account, category_hogar)
        # Create a second account and purchase
        acc2 = db.execute(
            "INSERT INTO cuentas (nombre, tipo) VALUES ('Mastercard', 'credito');"
        )
        ars_id = db.fetchone("SELECT id FROM monedas WHERE codigo = 'ARS';")["id"]
        db.execute(
            "INSERT INTO cuentas_saldos (cuenta_id, moneda_id, saldo_inicial_minor) VALUES (?, ?, 0);",
            (acc2, ars_id),
        )
        make_purchase(fees_service, acc2, category_hogar, concept="Other card purchase")

        rows = fees_service.list_purchases(account_id=credit_account)
        assert all(r["account_name"] == "Visa Galicia" for r in rows)

    def test_list_purchases_filters_by_estado(self, fees_service, credit_account, category_hogar):
        """list_purchases(estado='cancelada') returns only cancelled purchases."""
        result = make_purchase(fees_service, credit_account, category_hogar)
        fees_service.cancel_purchase(result.entity_id)
        rows = fees_service.list_purchases(estado="cancelada")
        assert any(r["id"] == result.entity_id for r in rows)

    def test_get_fees_for_purchase_returns_all_fees(self, fees_service, credit_account, category_hogar):
        """get_fees_for_purchase returns all N fee rows for a purchase."""
        result = make_purchase(fees_service, credit_account, category_hogar, total_fees=6)
        fees = fees_service.get_fees_for_purchase(result.entity_id)
        assert len(fees) == 6

    def test_get_fees_for_purchase_ordered_by_number(self, fees_service, credit_account, category_hogar):
        """Fees are returned ordered by numero_cuota ASC."""
        result = make_purchase(fees_service, credit_account, category_hogar, total_fees=5)
        fees = fees_service.get_fees_for_purchase(result.entity_id)
        numbers = [f["numero_cuota"] for f in fees]
        assert numbers == sorted(numbers)

    def test_get_fees_for_nonexistent_purchase_raises(self, fees_service):
        """get_fees_for_purchase on non-existent purchase raises PurchaseNotFoundError."""
        with pytest.raises(PurchaseNotFoundError):
            fees_service.get_fees_for_purchase(99999)


# =============================================================
# TEST: FEE PROJECTION
# =============================================================

class TestFeeProjection:

    def test_fees_by_month_returns_pending_fees(self, fees_service, credit_account, category_hogar):
        """fees_by_month returns fees for the specified month."""
        make_purchase(fees_service, credit_account, category_hogar,
                      date_str="2026-05-01", total_amount=120000.0, total_fees=12)
        rows = fees_service.fees_by_month(month=5, year=2026)
        assert len(rows) == 1
        assert rows[0]["account_name"] == "Visa Galicia"
        assert rows[0]["total_minor"]  == to_minor(10000.0)

    def test_fees_by_month_groups_by_account(self, fees_service, db, credit_account, category_hogar):
        """fees_by_month groups fees by account correctly."""
        make_purchase(fees_service, credit_account, category_hogar,
                      date_str="2026-05-01", total_amount=60000.0, total_fees=6)
        make_purchase(fees_service, credit_account, category_hogar,
                      concept="Second purchase",
                      date_str="2026-05-01", total_amount=120000.0, total_fees=12)
        rows = fees_service.fees_by_month(month=5, year=2026)
        # Both purchases are on the same account — should be grouped into 1 row
        assert len(rows) == 1
        assert rows[0]["total_minor"] == to_minor(10000.0 + 10000.0)

    def test_fees_by_month_wrong_month_returns_empty(self, fees_service, credit_account, category_hogar):
        """fees_by_month returns empty for a month with no fees."""
        make_purchase(fees_service, credit_account, category_hogar,
                      date_str="2026-05-01", total_fees=3)
        rows = fees_service.fees_by_month(month=9, year=2026)
        assert rows == []

    def test_fees_by_month_invalid_month_raises(self, fees_service):
        """Month outside 1–12 raises ValueError."""
        with pytest.raises(ValueError, match="Month must be between"):
            fees_service.fees_by_month(month=13, year=2026)

    def test_fees_projection_returns_n_months(self, fees_service, credit_account, category_hogar):
        """fees_projection returns exactly N month entries."""
        result = fees_service.fees_projection(months=6)
        assert len(result) == 6

    def test_fees_projection_months_are_consecutive(self, fees_service, credit_account, category_hogar):
        """fees_projection months are consecutive and wrap correctly."""
        today = datetime.today()
        result = fees_service.fees_projection(months=4)
        months = [(r["month"], r["year"]) for r in result]
        # Verify first entry matches today's month
        assert months[0][0] == today.month
        assert months[0][1] == today.year


# =============================================================
# TEST: STATEMENT LIFECYCLE
# =============================================================

class TestStatementLifecycle:

    def test_open_statement_creates_new(self, fees_service, db, credit_account):
        """open_statement creates a new statement row."""
        result = fees_service.open_statement(credit_account, month=5, year=2026)
        assert result.success is True
        row = db.fetchone(
            "SELECT * FROM resumenes_tarjeta WHERE id = ?;", (result.entity_id,)
        )
        assert row is not None
        assert row["mes"]  == 5
        assert row["anio"] == 2026

    def test_open_statement_is_idempotent(self, fees_service, credit_account):
        """Calling open_statement twice returns the same statement ID."""
        r1 = fees_service.open_statement(credit_account, month=5, year=2026)
        r2 = fees_service.open_statement(credit_account, month=5, year=2026)
        assert r1.entity_id == r2.entity_id
        assert r2.data["already_existed"] is True

    def test_open_statement_initial_estado_is_abierto(self, fees_service, db, credit_account):
        """New statement starts with estado='abierto'."""
        result = fees_service.open_statement(credit_account, month=5, year=2026)
        row = db.fetchone(
            "SELECT estado FROM resumenes_tarjeta WHERE id = ?;", (result.entity_id,)
        )
        assert row["estado"] == "abierto"

    def test_open_statement_initial_totals_are_zero(self, fees_service, db, credit_account):
        """New statement starts with zero totals."""
        result = fees_service.open_statement(credit_account, month=5, year=2026)
        row = db.fetchone(
            "SELECT monto_consumos_minor, monto_total_pagado_minor FROM resumenes_tarjeta WHERE id = ?;",
            (result.entity_id,),
        )
        assert row["monto_consumos_minor"]     == 0
        assert row["monto_total_pagado_minor"] == 0

    def test_confirm_fee_marks_en_resumen(self, fees_service, db, credit_account, category_hogar):
        """confirm_fee transitions the fee estado to 'en_resumen'."""
        purchase = make_purchase(fees_service, credit_account, category_hogar, total_fees=3)
        stmt     = fees_service.open_statement(credit_account, month=5, year=2026)
        fees     = fees_service.get_fees_for_purchase(purchase.entity_id)
        fees_service.confirm_fee(fees[0]["id"], stmt.entity_id)

        row = db.fetchone(
            "SELECT estado FROM cuotas_credito WHERE id = ?;", (fees[0]["id"],)
        )
        assert row["estado"] == "en_resumen"

    def test_confirm_fee_links_resumen_id(self, fees_service, db, credit_account, category_hogar):
        """confirm_fee sets resumen_id on the fee row."""
        purchase = make_purchase(fees_service, credit_account, category_hogar)
        stmt     = fees_service.open_statement(credit_account, month=5, year=2026)
        fees     = fees_service.get_fees_for_purchase(purchase.entity_id)
        fees_service.confirm_fee(fees[0]["id"], stmt.entity_id)

        row = db.fetchone(
            "SELECT resumen_id FROM cuotas_credito WHERE id = ?;", (fees[0]["id"],)
        )
        assert row["resumen_id"] == stmt.entity_id

    def test_confirm_fee_updates_statement_total(self, fees_service, db, credit_account, category_hogar):
        """confirm_fee increases monto_consumos_minor on the statement."""
        purchase = make_purchase(fees_service, credit_account, category_hogar,
                                 total_amount=120000.0, total_fees=12)
        stmt = fees_service.open_statement(credit_account, month=5, year=2026)
        fees = fees_service.get_fees_for_purchase(purchase.entity_id)
        fees_service.confirm_fee(fees[0]["id"], stmt.entity_id)

        row = db.fetchone(
            "SELECT monto_consumos_minor FROM resumenes_tarjeta WHERE id = ?;",
            (stmt.entity_id,),
        )
        assert row["monto_consumos_minor"] == to_minor(10000.0)

    def test_confirm_fee_stores_real_month(self, fees_service, db, credit_account, category_hogar):
        """confirm_fee stores real_month/real_year when provided (late fee tracking)."""
        purchase = make_purchase(fees_service, credit_account, category_hogar,
                                 date_str="2026-05-01", total_fees=3)
        stmt = fees_service.open_statement(credit_account, month=6, year=2026)
        fees = fees_service.get_fees_for_purchase(purchase.entity_id)

        # First fee was projected for May but appeared in June (skipped by bank)
        fees_service.confirm_fee(fees[0]["id"], stmt.entity_id, real_month=6, real_year=2026)

        row = db.fetchone(
            "SELECT mes_real_pago, anio_real_pago FROM cuotas_credito WHERE id = ?;",
            (fees[0]["id"],),
        )
        assert row["mes_real_pago"]  == 6
        assert row["anio_real_pago"] == 2026

    def test_confirm_fee_already_confirmed_raises(self, fees_service, credit_account, category_hogar):
        """Confirming a fee that's already en_resumen raises FeeAlreadyConfirmedError."""
        purchase = make_purchase(fees_service, credit_account, category_hogar)
        stmt     = fees_service.open_statement(credit_account, month=5, year=2026)
        fees     = fees_service.get_fees_for_purchase(purchase.entity_id)
        fees_service.confirm_fee(fees[0]["id"], stmt.entity_id)

        with pytest.raises(FeeAlreadyConfirmedError):
            fees_service.confirm_fee(fees[0]["id"], stmt.entity_id)

    def test_confirm_fee_nonexistent_fee_raises(self, fees_service, credit_account):
        """Confirming a non-existent fee raises FeeNotFoundError."""
        stmt = fees_service.open_statement(credit_account, month=5, year=2026)
        with pytest.raises(FeeNotFoundError):
            fees_service.confirm_fee(99999, stmt.entity_id)

    def test_confirm_fee_nonexistent_statement_raises(self, fees_service, credit_account, category_hogar):
        """Confirming against a non-existent statement raises StatementNotFoundError."""
        purchase = make_purchase(fees_service, credit_account, category_hogar)
        fees = fees_service.get_fees_for_purchase(purchase.entity_id)
        with pytest.raises(StatementNotFoundError):
            fees_service.confirm_fee(fees[0]["id"], 99999)

    def test_close_statement_transitions_to_cerrado(self, fees_service, db, credit_account, category_hogar):
        """close_statement sets estado to 'cerrado'."""
        purchase, stmt_id, _ = full_statement_cycle(fees_service, credit_account, category_hogar)
        row = db.fetchone(
            "SELECT estado FROM resumenes_tarjeta WHERE id = ?;", (stmt_id,)
        )
        assert row["estado"] == "cerrado"

    def test_close_already_paid_statement_raises(self, fees_service, credit_account, category_hogar):
        """Closing an already paid statement raises StatementAlreadyPaidError."""
        purchase, stmt_id, _ = full_statement_cycle(fees_service, credit_account, category_hogar)
        fees_service.pay_statement(stmt_id, payment_date="2026-05-30")
        with pytest.raises(StatementAlreadyPaidError):
            fees_service.close_statement(stmt_id)

    def test_pay_statement_transitions_to_pagado(self, fees_service, db, credit_account, category_hogar):
        """pay_statement sets estado to 'pagado'."""
        purchase, stmt_id, _ = full_statement_cycle(fees_service, credit_account, category_hogar)
        fees_service.pay_statement(stmt_id, payment_date="2026-05-30")

        row = db.fetchone(
            "SELECT estado FROM resumenes_tarjeta WHERE id = ?;", (stmt_id,)
        )
        assert row["estado"] == "pagado"

    def test_pay_statement_stores_payment_date(self, fees_service, db, credit_account, category_hogar):
        """pay_statement stores the payment date."""
        purchase, stmt_id, _ = full_statement_cycle(fees_service, credit_account, category_hogar)
        fees_service.pay_statement(stmt_id, payment_date="2026-05-30")

        row = db.fetchone(
            "SELECT fecha_pago FROM resumenes_tarjeta WHERE id = ?;", (stmt_id,)
        )
        assert row["fecha_pago"] == "2026-05-30"

    def test_pay_statement_marks_fees_pagado(self, fees_service, db, credit_account, category_hogar):
        """pay_statement transitions all en_resumen fees to 'pagado'."""
        purchase, stmt_id, fee_id = full_statement_cycle(fees_service, credit_account, category_hogar)
        fees_service.pay_statement(stmt_id, payment_date="2026-05-30")

        row = db.fetchone(
            "SELECT estado FROM cuotas_credito WHERE id = ?;", (fee_id,)
        )
        assert row["estado"] == "pagado"

    def test_pay_statement_with_transaction_id(self, fees_service, credit_account, category_hogar):
        """pay_statement result includes the transaction_id when provided."""
        purchase, stmt_id, _ = full_statement_cycle(fees_service, credit_account, category_hogar)
        result = fees_service.pay_statement(stmt_id, payment_date="2026-05-30", transaction_id=77)
        assert result.data["transaction_id"] == 77

    def test_pay_already_paid_statement_raises(self, fees_service, credit_account, category_hogar):
        """Paying an already paid statement raises StatementAlreadyPaidError."""
        purchase, stmt_id, _ = full_statement_cycle(fees_service, credit_account, category_hogar)
        fees_service.pay_statement(stmt_id, payment_date="2026-05-30")
        with pytest.raises(StatementAlreadyPaidError):
            fees_service.pay_statement(stmt_id, payment_date="2026-06-01")

    def test_confirm_fee_on_paid_statement_raises(self, fees_service, credit_account, category_hogar):
        """Confirming a fee on an already paid statement raises StatementAlreadyPaidError."""
        purchase = make_purchase(fees_service, credit_account, category_hogar, total_fees=3)
        stmt     = fees_service.open_statement(credit_account, month=5, year=2026)
        fees     = fees_service.get_fees_for_purchase(purchase.entity_id)

        fees_service.confirm_fee(fees[0]["id"], stmt.entity_id)
        fees_service.close_statement(stmt.entity_id)
        fees_service.pay_statement(stmt.entity_id, payment_date="2026-05-30")

        with pytest.raises(StatementAlreadyPaidError):
            fees_service.confirm_fee(fees[1]["id"], stmt.entity_id)

    def test_get_statement_returns_enriched_row(self, fees_service, credit_account):
        """get_statement returns a row with account_name."""
        stmt = fees_service.open_statement(credit_account, month=5, year=2026)
        row  = fees_service.get_statement(stmt.entity_id)
        assert row is not None
        assert row["account_name"] == "Visa Galicia"

    def test_get_statement_nonexistent_returns_none(self, fees_service):
        """get_statement returns None for a non-existent ID."""
        assert fees_service.get_statement(99999) is None

    def test_list_statements_filters_by_estado(self, fees_service, credit_account, category_hogar):
        """list_statements(estado='pagado') returns only paid statements."""
        purchase, stmt_id, _ = full_statement_cycle(fees_service, credit_account, category_hogar)
        fees_service.pay_statement(stmt_id, payment_date="2026-05-30")
        rows = fees_service.list_statements(estado="pagado")
        assert any(r["id"] == stmt_id for r in rows)
        assert all(r["estado"] == "pagado" for r in rows)

    def test_list_statements_filters_by_year(self, fees_service, credit_account):
        """list_statements(year=2026) excludes statements from other years."""
        fees_service.open_statement(credit_account, month=5, year=2026)
        fees_service.open_statement(credit_account, month=3, year=2025)
        rows = fees_service.list_statements(year=2026)
        assert all(r["anio"] == 2026 for r in rows)


# =============================================================
# TEST: CANCEL PURCHASE
# =============================================================

class TestCancelPurchase:

    def test_cancel_marks_purchase_cancelada(self, fees_service, db, credit_account, category_hogar):
        """cancel_purchase sets estado to 'cancelada'."""
        result = make_purchase(fees_service, credit_account, category_hogar, total_fees=6)
        fees_service.cancel_purchase(result.entity_id, notes="Changed mind")
        row = db.fetchone(
            "SELECT estado FROM compras_cuotas WHERE id = ?;", (result.entity_id,)
        )
        assert row["estado"] == "cancelada"

    def test_cancel_marks_pending_fees_omitido(self, fees_service, db, credit_account, category_hogar):
        """cancel_purchase marks all pending fees as 'omitido'."""
        result = make_purchase(fees_service, credit_account, category_hogar, total_fees=6)
        fees_service.cancel_purchase(result.entity_id)
        rows = db.fetchall(
            "SELECT estado FROM cuotas_credito WHERE compra_id = ?;", (result.entity_id,)
        )
        assert all(r["estado"] == "omitido" for r in rows)

    def test_cancel_result_has_fees_cancelled_count(self, fees_service, credit_account, category_hogar):
        """cancel_purchase result data includes the count of cancelled fees."""
        result = make_purchase(fees_service, credit_account, category_hogar, total_fees=6)
        cancel = fees_service.cancel_purchase(result.entity_id)
        assert cancel.data["fees_cancelled"] == 6

    def test_cancel_does_not_affect_confirmed_fees(self, fees_service, db, credit_account, category_hogar):
        """cancel_purchase leaves fees already en_resumen untouched."""
        result = make_purchase(fees_service, credit_account, category_hogar, total_fees=3)
        stmt   = fees_service.open_statement(credit_account, month=5, year=2026)
        fees   = fees_service.get_fees_for_purchase(result.entity_id)

        # Confirm first fee before cancelling
        fees_service.confirm_fee(fees[0]["id"], stmt.entity_id)
        fees_service.cancel_purchase(result.entity_id)

        confirmed_row = db.fetchone(
            "SELECT estado FROM cuotas_credito WHERE id = ?;", (fees[0]["id"],)
        )
        assert confirmed_row["estado"] == "en_resumen"  # untouched

    def test_cancel_already_cancelled_raises(self, fees_service, credit_account, category_hogar):
        """Cancelling an already cancelled purchase raises FeesError."""
        result = make_purchase(fees_service, credit_account, category_hogar)
        fees_service.cancel_purchase(result.entity_id)
        with pytest.raises(FeesError):
            fees_service.cancel_purchase(result.entity_id)

    def test_cancel_nonexistent_raises(self, fees_service):
        """Cancelling a non-existent purchase raises PurchaseNotFoundError."""
        with pytest.raises(PurchaseNotFoundError):
            fees_service.cancel_purchase(99999)