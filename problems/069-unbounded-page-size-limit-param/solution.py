"""Cap the page size a client can ask for on a list endpoint.

The orders listing took ``?limit=`` straight from the query string. A partner
integration passed ``limit=500000`` to "get everything in one call", and the
API loaded every order into memory and serialised it while the database ran
at full CPU. ``limit=-1`` was just as bad: Python slicing with ``[:-1]``
returns every row except the last one.

The fix parses the limit strictly: it must be a whole number of at least 1,
values above ``MAX_PAGE_SIZE`` are clamped, and the response says which limit
was applied and where the next page starts, so clients page instead of
asking for everything.
"""
from __future__ import annotations

from dataclasses import dataclass

HTTP_OK = 200
HTTP_BAD_REQUEST = 400
DEFAULT_PAGE_SIZE = 50
# Largest page we are willing to load and serialise in one request.
MAX_PAGE_SIZE = 200


@dataclass(frozen=True)
class Response:
    status: int
    body: dict


class OrderStore:
    """In-memory orders table that counts how many rows each query reads."""

    def __init__(self, order_count: int) -> None:
        self._order_ids = list(range(1, order_count + 1))
        self.rows_read = 0

    def page(self, offset: int, limit: int) -> list[int]:
        """Return up to ``limit`` order ids starting at ``offset``."""
        rows = self._order_ids[offset:offset + limit]
        self.rows_read += len(rows)
        return rows

    def naive_page(self, raw_limit: int) -> list[int]:
        """Original query: slices with whatever number the client sent."""
        rows = self._order_ids[:raw_limit]
        self.rows_read += len(rows)
        return rows


# --- the original, buggy version ---------------------------------------------
def naive_list_orders(query: dict[str, str], store: OrderStore) -> Response:
    """Original handler: trusts the client's limit completely."""
    limit = int(query.get("limit", DEFAULT_PAGE_SIZE))
    return Response(HTTP_OK, {"items": store.naive_page(limit)})


# --- the fix -----------------------------------------------------------------
class InvalidParameter(ValueError):
    """A query parameter that the client must correct."""


def parse_positive_int(name: str, raw_value: str) -> int:
    """Parse a decimal whole number, rejecting signs, spaces and floats."""
    # str.isdigit() is False for "-1", "1.5", " 5" and "", so all of them are
    # rejected here instead of being half-accepted by int().
    if not raw_value.isascii() or not raw_value.isdigit():
        raise InvalidParameter(f"{name} must be a whole number, got {raw_value!r}")
    return int(raw_value)


def parse_limit(query: dict[str, str]) -> int:
    """Return the page size to use: default if absent, clamped to the maximum."""
    if "limit" not in query:
        return DEFAULT_PAGE_SIZE
    limit = parse_positive_int("limit", query["limit"])
    if limit < 1:
        raise InvalidParameter("limit must be at least 1")
    # Clamping (rather than rejecting) keeps old clients working; the applied
    # limit is echoed back so they can see they got less than they asked for.
    return min(limit, MAX_PAGE_SIZE)


def parse_offset(query: dict[str, str]) -> int:
    """Return the start position; 0 when absent."""
    if "offset" not in query:
        return 0
    return parse_positive_int("offset", query["offset"])


def list_orders(query: dict[str, str], store: OrderStore) -> Response:
    """Fixed handler: bounded page size and an explicit next-page pointer."""
    try:
        limit = parse_limit(query)
        offset = parse_offset(query)
    except InvalidParameter as error:
        return Response(HTTP_BAD_REQUEST, {"error": str(error)})
    items = store.page(offset, limit)
    # A full page means there may be more; a short page means we reached the end.
    next_offset = offset + limit if len(items) == limit else None
    return Response(HTTP_OK, {"items": items, "limit": limit, "next_offset": next_offset})
