"""Pagination cursors that only work with the query they were issued for.

The order-list API returned an opaque ``next_cursor`` that held only the
position of the last row (its sort value and id). The cursor did not record
which filters and sort order produced that position. When the dashboard
changed the status filter or the sort order mid-scroll but kept the old cursor,
the server applied the old position to the new query. Rows were silently
skipped, and an empty page looked like "no more results".

The fix puts a fingerprint of the filters and sort order inside the cursor,
signs the cursor with HMAC, and rejects a cursor whose fingerprint does not
match the current request with a clear 400-style error.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass

SORT_NEWEST = "newest"
SORT_LARGEST_TOTAL = "largest_total"
SUPPORTED_SORTS = (SORT_NEWEST, SORT_LARGEST_TOTAL)
SIGNING_KEY_BYTES = 32
SIGNATURE_HEX_CHARS = 32
FINGERPRINT_HEX_CHARS = 16


@dataclass(frozen=True)
class Order:
    id: int
    status: str
    created_at: int
    total_cents: int


@dataclass
class Page:
    orders: list[Order]
    next_cursor: str | None


class InvalidCursor(ValueError):
    """The cursor is malformed or was tampered with (HTTP 400)."""


class CursorQueryMismatch(InvalidCursor):
    """The cursor belongs to a different filter/sort (HTTP 400, restart from page 1)."""


def sort_key(order: Order, sort: str) -> tuple[int, int]:
    """Return the (value, id) pair the list is ordered by, largest first.

    The id breaks ties so rows with equal values are never skipped or repeated.
    """
    if sort == SORT_NEWEST:
        return (order.created_at, order.id)
    if sort == SORT_LARGEST_TOTAL:
        return (order.total_cents, order.id)
    raise ValueError(f"unsupported sort {sort!r}; use one of {SUPPORTED_SORTS}")


def newest_key(order: Order) -> tuple[int, int]:
    """Sort key for the "newest" order."""
    return sort_key(order, SORT_NEWEST)


def largest_total_key(order: Order) -> tuple[int, int]:
    """Sort key for the "largest_total" order."""
    return sort_key(order, SORT_LARGEST_TOTAL)


SORT_KEY_GETTERS = {SORT_NEWEST: newest_key, SORT_LARGEST_TOTAL: largest_total_key}


def select_page(orders: list[Order], status: str | None, sort: str,
                after: tuple[int, int] | None, page_size: int) -> tuple[list[Order], bool]:
    """Filter, sort and cut one page (keyset style). Returns (rows, has_more)."""
    matching = []
    for order in orders:
        if status is not None and order.status != status:
            continue
        # Keyset pagination: keep only rows strictly after the last row we sent.
        if after is not None and sort_key(order, sort) >= after:
            continue
        matching.append(order)
    matching.sort(key=SORT_KEY_GETTERS[sort], reverse=True)
    return matching[:page_size], len(matching) > page_size


# --- the original, buggy version ---------------------------------------------
def naive_list_orders(orders: list[Order], status: str | None, sort: str,
                      cursor: str | None, page_size: int) -> Page:
    """Cursor holds only the position, so it is silently reused for any query."""
    after = None
    if cursor is not None:
        after = tuple(json.loads(base64.urlsafe_b64decode(cursor)))
    rows, has_more = select_page(orders, status, sort, after, page_size)
    next_cursor = None
    if has_more:
        position = list(sort_key(rows[-1], sort))
        next_cursor = base64.urlsafe_b64encode(json.dumps(position).encode()).decode()
    return Page(rows, next_cursor)


# --- the fix -----------------------------------------------------------------
def query_fingerprint(status: str | None, sort: str) -> str:
    """Short hash of everything that decides which rows exist and their order.

    Page size is left out on purpose: changing it does not move the position.
    """
    canonical = json.dumps({"status": status, "sort": sort}, sort_keys=True)
    return hashlib.sha256(canonical.encode()).hexdigest()[:FINGERPRINT_HEX_CHARS]


class CursorCodec:
    """Encodes ``{fingerprint, position}`` as ``base64(json).signature``."""

    def __init__(self, signing_key: bytes | None = None) -> None:
        # Generated at runtime for this demo. In production every API instance
        # must load the same key from a secret store, or cursors break across them.
        self._key = signing_key or secrets.token_bytes(SIGNING_KEY_BYTES)

    def _sign(self, payload: str) -> str:
        digest = hmac.new(self._key, payload.encode(), hashlib.sha256).hexdigest()
        return digest[:SIGNATURE_HEX_CHARS]

    def encode(self, fingerprint: str, position: tuple[int, int]) -> str:
        """Build an opaque, signed cursor."""
        body = json.dumps({"fp": fingerprint, "pos": list(position)}, separators=(",", ":"))
        payload = base64.urlsafe_b64encode(body.encode()).decode()
        return f"{payload}.{self._sign(payload)}"

    def decode(self, cursor: str, expected_fingerprint: str) -> tuple[int, int]:
        """Verify the signature, then check the cursor matches this query."""
        payload, _, signature = cursor.rpartition(".")
        # compare_digest raises TypeError on non-ASCII str, so check first.
        if not cursor.isascii() or not payload:
            raise InvalidCursor("malformed cursor")
        if not hmac.compare_digest(signature, self._sign(payload)):
            raise InvalidCursor("cursor signature does not match")
        data = self._parse_payload(payload)
        if data["fp"] != expected_fingerprint:
            raise CursorQueryMismatch(
                "cursor was issued for different filters or sort order; "
                "request the first page again without a cursor")
        return data["pos"][0], data["pos"][1]

    @staticmethod
    def _parse_payload(payload: str) -> dict:
        """Decode the JSON body and check its shape."""
        try:
            data = json.loads(base64.urlsafe_b64decode(payload))
        except (binascii.Error, ValueError) as error:
            raise InvalidCursor("undecodable cursor") from error
        position = data.get("pos") if isinstance(data, dict) else None
        valid_position = (isinstance(position, list) and len(position) == 2
                          and all(type(item) is int for item in position))
        if not valid_position or not isinstance(data.get("fp"), str):
            raise InvalidCursor("cursor has the wrong shape")
        return data


def list_orders(orders: list[Order], codec: CursorCodec, status: str | None, sort: str,
                cursor: str | None, page_size: int) -> Page:
    """List orders; a cursor is accepted only for the query that issued it."""
    if sort not in SUPPORTED_SORTS:
        raise ValueError(f"unsupported sort {sort!r}; use one of {SUPPORTED_SORTS}")
    fingerprint = query_fingerprint(status, sort)
    after = None
    if cursor is not None:
        after = codec.decode(cursor, fingerprint)
    rows, has_more = select_page(orders, status, sort, after, page_size)
    next_cursor = None
    if has_more:
        next_cursor = codec.encode(fingerprint, sort_key(rows[-1], sort))
    return Page(rows, next_cursor)
