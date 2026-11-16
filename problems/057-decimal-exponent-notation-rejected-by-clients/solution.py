"""Money amounts serialised in exponent notation ("1E+1") break mobile clients.

The wallet API sends amounts as JSON strings built from Python Decimals. A
change to strip trailing zeros used Decimal.normalize(), which turns 10.00 into
"1E+1", and str() on its own already prints tiny per-unit prices such as
0.00000015 as "1.5E-7". The mobile apps only accept plain digits with an
optional decimal point, so balances failed to render. This module formats
every amount in fixed-point with the currency's number of decimal places, and
refuses values that cannot be shown exactly instead of silently rounding.
"""
from __future__ import annotations

import re
from decimal import Decimal, Inexact, InvalidOperation, localcontext

# Decimal places per currency (ISO 4217 minor units).
CURRENCY_DECIMAL_PLACES: dict[str, int] = {
    "USD": 2,
    "EUR": 2,
    "INR": 2,
    "JPY": 0,
    "KWD": 3,
}
# Per-unit prices (e.g. per API call) need more places than any currency.
UNIT_PRICE_DECIMAL_PLACES = 8
# Enough precision for any realistic amount, so quantize never fails for size.
WORKING_PRECISION = 38

# The pattern the mobile apps validate amount strings against.
CLIENT_AMOUNT_PATTERN = re.compile(r"^-?\d+(\.\d+)?$")


class AmountFormatError(ValueError):
    """Raised when an amount cannot be represented exactly for its currency."""


def naive_format_amount(amount: Decimal) -> str:
    """Original serialiser: strip trailing zeros with normalize(), then str().

    normalize() removes trailing zeros by raising the exponent, so 10.00
    becomes 1E+1, and str() prints exponents for very small values.
    """
    return str(amount.normalize())


def client_accepts(amount_text: str) -> bool:
    """Return True if the mobile app's validator accepts this amount string."""
    return CLIENT_AMOUNT_PATTERN.fullmatch(amount_text) is not None


def decimal_places_for(currency: str) -> int:
    """Look up the currency's decimal places; unknown codes are a bug, not a guess."""
    if currency not in CURRENCY_DECIMAL_PLACES:
        raise AmountFormatError(f"unknown currency {currency!r}")
    return CURRENCY_DECIMAL_PLACES[currency]


def quantize_exactly(amount: Decimal, decimal_places: int) -> Decimal:
    """Give ``amount`` exactly ``decimal_places`` digits after the point.

    Padding with zeros is always safe. Dropping non-zero digits would change
    the money value, so the Inexact signal is trapped and reported instead.
    """
    if not amount.is_finite():
        raise AmountFormatError(f"amount must be a finite number, got {amount}")
    step = Decimal(1).scaleb(-decimal_places)
    with localcontext() as context:
        context.prec = WORKING_PRECISION
        context.traps[Inexact] = True
        try:
            return amount.quantize(step)
        except (Inexact, InvalidOperation) as error:
            raise AmountFormatError(
                f"{amount} has more than {decimal_places} decimal places"
            ) from error


def to_fixed_point_text(amount: Decimal) -> str:
    """Render a Decimal with no exponent; "-0.00" becomes "0.00"."""
    # A negative zero (e.g. from a refund of 0.00) would confuse clients.
    if amount.is_zero():
        amount = abs(amount)
    # The "f" format never uses exponent notation, unlike str().
    return format(amount, "f")


def format_amount(amount: Decimal, currency: str) -> str:
    """Format a money amount for the API: fixed-point, currency decimal places."""
    exact = quantize_exactly(amount, decimal_places_for(currency))
    return to_fixed_point_text(exact)


def format_unit_price(price: Decimal) -> str:
    """Format a per-unit price, which may be far smaller than one cent."""
    exact = quantize_exactly(price, UNIT_PRICE_DECIMAL_PLACES)
    return to_fixed_point_text(exact)
