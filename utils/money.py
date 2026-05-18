def amount_display(
    amount_minor: int,
    decimals: int = 2,
    currency_symbol: str = "$"
) -> str:

    amount = amount_minor / (10 ** decimals)

    return f"{currency_symbol}{amount:,.2f}"