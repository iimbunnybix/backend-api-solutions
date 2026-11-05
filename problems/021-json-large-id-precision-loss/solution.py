"""Order API that sends 64-bit IDs to JavaScript clients without corrupting them.

Order IDs are 64-bit integers from a Snowflake-style generator. The original API
wrote them into JSON as plain numbers. JavaScript's JSON.parse turns every
number into an IEEE-754 double, which holds integers exactly only up to 2**53,
so IDs above that were silently rounded: the web app opened, cancelled or
refunded a *different* order whose ID happened to round to the same value.
This version sends IDs as decimal strings and parses incoming IDs strictly,
accepting both strings (new clients) and exact integers (old clients).
"""
from __future__ import annotations

import json
from typing import Any

# Largest integer a double (JavaScript Number) represents exactly: 2**53 - 1.
JAVASCRIPT_MAX_SAFE_INTEGER = 2**53 - 1

# IDs are stored in a signed 64-bit BIGINT column.
MAX_ID = 2**63 - 1

# Fields in API responses that carry database IDs.
ID_FIELDS = ("id", "customer_id")


class InvalidId(ValueError):
    """Raised when a client sends something that is not a valid ID (HTTP 400)."""


def naive_serialize_order(order: dict[str, Any]) -> str:
    """Original response encoder: IDs go out as JSON numbers."""
    return json.dumps(order)


def serialize_order(order: dict[str, Any]) -> str:
    """Fixed response encoder: every ID field goes out as a decimal string.

    Strings survive any JSON parser unchanged, so the client gets back exactly
    the digits the database produced, whatever its number type is.
    """
    safe_order = dict(order)
    for field in ID_FIELDS:
        if field in safe_order:
            safe_order[field] = str(safe_order[field])
    return json.dumps(safe_order)


def _integer_as_javascript_number(digits: str) -> int:
    """Round an integer literal through a double, exactly as JSON.parse does."""
    return int(float(digits))


def parse_like_javascript(json_text: str) -> dict[str, Any]:
    """Decode JSON the way a browser does: every number becomes a double.

    This is a test helper that shows what the web app actually sees.
    """
    return json.loads(json_text, parse_int=_integer_as_javascript_number)


def is_javascript_safe(value: int) -> bool:
    """True if a JavaScript client can hold this integer without rounding."""
    return -JAVASCRIPT_MAX_SAFE_INTEGER <= value <= JAVASCRIPT_MAX_SAFE_INTEGER


def parse_id(raw: Any) -> int:
    """Turn an ID from a request body or URL into an int, or raise InvalidId.

    Accepts decimal strings ("1891...") and ints. Rejects floats because a
    float ID has usually already been rounded by the client, and rejects
    bools because Python treats True as the int 1.
    """
    if isinstance(raw, bool):
        raise InvalidId("ID must not be a boolean")
    if isinstance(raw, int):
        return _check_range(raw)
    if isinstance(raw, str):
        return _check_range(_parse_decimal_string(raw))
    raise InvalidId(f"ID must be a string or integer, got {type(raw).__name__}")


def _parse_decimal_string(raw: str) -> int:
    """Parse ASCII digits only; int() alone would accept " 12", "+12", "1_2" and "١٢"."""
    if not raw.isascii() or not raw.isdigit():
        raise InvalidId(f"ID must be ASCII digits, got {raw!r}")
    return int(raw)


def _check_range(value: int) -> int:
    """IDs are positive and must fit the BIGINT column."""
    if value < 1 or value > MAX_ID:
        raise InvalidId(f"ID out of range: {value}")
    return value
