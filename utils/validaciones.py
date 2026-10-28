"""
DeltaBalance — utils/validaciones.py

Excepciones y helpers de validación que estaban copiados igual en varios
services. Cada service sigue lanzando la excepción de SU dominio (los
`except` de la UI no cambian): los helpers que validan reciben la clase de
error por parámetro, y el service los ata con functools.partial, ej.

    _concepto = staticmethod(partial(validate_concept, error=IngresoError))

Excepciones comunes: AccountNotFoundError, CategoryNotFoundError y
CurrencyNotFoundError. Todavía no las lanza nadie — hoy cada service tiene
las suyas (mismo nombre, subclase del error de su dominio). Se usan cuando
se unifiquen _get_account / _get_category / _get_currency (Nivel 3).
"""

from datetime import date, datetime
from typing import Any, Optional


# =============================================================
# EXCEPCIONES COMUNES
# =============================================================

class AccountNotFoundError(Exception):
    """La cuenta no existe."""


class CategoryNotFoundError(Exception):
    """La categoría no existe."""


class CurrencyNotFoundError(Exception):
    """La moneda no existe."""


# =============================================================
# HELPERS DE VALIDACIÓN
# =============================================================

def validate_date(date_str) -> str:
    """
    Validates and normalizes a date to 'YYYY-MM-DD'.
    Accepts date/datetime objects or strings.

    Antes: FeesService._validate_date / TransactionService._validate_date.

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


def validate_concept(concepto: Optional[str], error: type[Exception] = ValueError) -> str:
    """
    Concepto sin espacios de más y en mayúsculas; vacío → `error`.

    Antes: IngresosService._concepto / PresupuestosService._concepto.
    """
    limpio = " ".join((concepto or "").split()).upper()
    if not limpio:
        raise error("EL CONCEPTO NO PUEDE ESTAR VACÍO.")
    return limpio


def validate_period(mes: Any, anio: Any, error: type[Exception] = ValueError) -> tuple[int, int]:
    """
    Mes y año enteros (no bool), mes entre 1 y 12 → (mes, anio).

    Antes: IngresosService._periodo / PresupuestosService._periodo.
    (SnapshotsService._periodo es otra regla: devuelve (anio, mes).)
    """
    for valor in (mes, anio):
        if not isinstance(valor, int) or isinstance(valor, bool):
            raise error(f"MES Y AÑO TIENEN QUE SER ENTEROS (RECIBIDO: {valor!r}).")
    if not 1 <= mes <= 12:
        raise error(f"EL MES TIENE QUE ESTAR ENTRE 1 Y 12 (RECIBIDO: {mes}).")
    return mes, anio


def recurring_flag(valor: Any) -> int:
    """
    es_recurrente como 0/1 para la columna INTEGER.

    Antes: IngresosService._recurrente / PresupuestosService._recurrente.
    """
    return 1 if valor else 0
