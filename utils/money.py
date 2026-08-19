def amount_display(
    amount_minor: int,
    decimals: int = 2,
    currency_symbol: str = "$"
) -> str:

    amount = amount_minor / (10 ** decimals)

    return f"{currency_symbol}{amount:,.2f}"


def amount_to_minor(amount: float, decimals: int = 2) -> int:
    """
    Float -> minor units. Misma fórmula que db.database.to_minor() — vive
    también acá porque ui/ no puede importar de db/ directo (CLAUDE.md §2),
    y algún service (ej. FeesService.add_extra_charge()) recibe el monto ya
    en minor units en vez de resolverlo internamente como create_purchase().
    """
    return round(amount * (10 ** decimals))