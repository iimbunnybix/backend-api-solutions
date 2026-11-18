"""Parse uploaded prices with the seller's declared number format.

The bulk price-import endpoint cleaned each value with
``float(text.replace(",", ""))``, assuming commas are always thousands
separators. Sellers in Germany and India-based teams using European templates
write "12,50" for twelve and a half, which became 1250.0, a price 100 times too
high. This module parses with an explicit decimal and grouping separator per
seller, checks the digit grouping strictly, rejects anything it cannot read
without guessing, and returns Decimal so no float rounding is added.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal

MAX_FRACTION_DIGITS = 2


class PriceFormatError(ValueError):
    """The value is not a valid price in the seller's number format."""


@dataclass(frozen=True)
class NumberFormat:
    decimal_separator: str
    group_separator: str


# The two formats the seller settings page offers.
EN_FORMAT = NumberFormat(decimal_separator=".", group_separator=",")   # 1,234.50
DE_FORMAT = NumberFormat(decimal_separator=",", group_separator=".")   # 1.234,50


def naive_parse_price(text: str) -> float:
    """Original code: drop every comma, then parse as a float."""
    return float(text.replace(",", ""))


def _price_pattern(number_format: NumberFormat) -> re.Pattern[str]:
    """Regex for a price: plain digits or groups of exactly three, plus cents.

    Requiring exact groups of three is what catches a German "12,50" sent to an
    English-format seller: "12,50" is not a valid "1,234" style group.
    """
    # [0-9], not \d: \d also matches non-ASCII digits such as "١٢".
    group = re.escape(number_format.group_separator)
    decimal = re.escape(number_format.decimal_separator)
    whole_part = rf"(?:[0-9]+|[0-9]{{1,3}}(?:{group}[0-9]{{3}})+)"
    fraction_part = rf"(?:{decimal}[0-9]{{1,{MAX_FRACTION_DIGITS}}})?"
    return re.compile(rf"{whole_part}{fraction_part}")


def parse_price(text: str, number_format: NumberFormat) -> Decimal:
    """Parse one non-negative price written in ``number_format``.

    Raises PriceFormatError instead of guessing, so a bad row is reported back
    to the seller rather than published at the wrong price.
    """
    cleaned = text.strip()
    if not _price_pattern(number_format).fullmatch(cleaned):
        raise PriceFormatError(f"{text!r} is not a price like {example(number_format)}")
    # Remove grouping first, then turn the decimal separator into ".".
    without_groups = cleaned.replace(number_format.group_separator, "")
    canonical = without_groups.replace(number_format.decimal_separator, ".")
    return Decimal(canonical)


def example(number_format: NumberFormat) -> str:
    """Human-readable example used in error messages, e.g. 1.234,50."""
    return f"1{number_format.group_separator}234{number_format.decimal_separator}50"


@dataclass
class ImportResult:
    prices: dict[str, Decimal]
    errors: dict[str, str]


def import_prices(rows: list[tuple[str, str]], number_format: NumberFormat) -> ImportResult:
    """Parse (sku, price text) rows; collect per-row errors instead of failing all."""
    result = ImportResult(prices={}, errors={})
    for sku, price_text in rows:
        try:
            result.prices[sku] = parse_price(price_text, number_format)
        except PriceFormatError as error:
            result.errors[sku] = str(error)
    return result
