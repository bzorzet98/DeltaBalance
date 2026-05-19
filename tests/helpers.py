# tests/helpers.py
# =============================================================
# HELPERS
# =============================================================
def make_debt(debt_service, person="Noe", debt_type="a_favor", amount=5000.0,
              currency_code="ARS", date_str="2026-05-10", concept="Shared dinner"):
    """Creates a standard debt. Reused across many tests."""
    return debt_service.create(
        person=person,
        debt_type=debt_type,
        amount=amount,
        currency_code=currency_code,
        date_str=date_str,
        concept=concept,
    )

def make_payment(debt_service, debt_id, amount=1000.0, currency_code="ARS",
                 date_str="2026-05-15", payment_type="transaccion"):
    """Registers a standard payment. Reused across many tests."""
    return debt_service.register_payment(
        debt_id=debt_id,
        amount=amount,
        currency_code=currency_code,
        date_str=date_str,
        payment_type=payment_type,
    )

def make_expense(transaction_service, account_id, category_id, amount=1000.0, date_str="2026-05-10"):
    """One-liner to create a standard ARS egreso. Reused across many tests."""
    return transaction_service.create(
        date_str=date_str,
        concept="Test expense",
        account_id=account_id,
        category_id=category_id,
        currency_code="ARS",
        amount=amount,
        movement_type="egreso",
    )

def make_purchase(fees_service, credit_account, category_hogar,
                  concept="Washing machine", total_amount=120000.0,
                  total_fees=12, date_str="2026-05-01", currency_code="ARS"):
    """Creates a standard purchase in installments."""
    return fees_service.create_purchase(
        date_str=date_str,
        concept=concept,
        account_id=credit_account,
        category_id=category_hogar,
        currency_code=currency_code,
        total_amount=total_amount,
        total_fees=total_fees,
    )

def full_statement_cycle(fees_service, credit_account, category_hogar, month=5, year=2026):
    """
    Helper that runs the full statement lifecycle:
    purchase → open statement → confirm first fee → close → pay.
    Returns (purchase_result, statement_id, fee_id).
    """
    purchase = make_purchase(fees_service, credit_account, category_hogar,
                             date_str=f"{year}-{month:02d}-01")
    stmt   = fees_service.open_statement(credit_account, month, year)
    fees   = fees_service.get_fees_for_purchase(purchase.entity_id)
    fee_id = fees[0]["id"]
    fees_service.confirm_fee(fee_id, stmt.entity_id)
    fees_service.close_statement(stmt.entity_id)
    return purchase, stmt.entity_id, fee_id

