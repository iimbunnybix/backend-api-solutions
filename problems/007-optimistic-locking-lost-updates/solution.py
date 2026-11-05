"""Optimistic concurrency control for a product-catalog API (ETag / If-Match).

The original bug was a classic *lost update*: the admin UI loaded a product,
the operator edited it, and the UI sent the whole record back with ``PUT``.
The handler blindly wrote every field. When two people (or a person and the
stock-sync job) edited the same product at the same time, whoever saved last
silently overwrote the other's change.

Fix: every row carries a ``version``. Reads return it as an ``ETag``. Writes
must send it back in ``If-Match`` and the database applies the write with a
conditional ``UPDATE ... WHERE id = ? AND version = ?``. If another writer got
there first, zero rows match and the API answers ``412 Precondition Failed``
instead of overwriting. Writes without ``If-Match`` are refused with ``428``.
For machine writers (e.g. stock decrements) ``update_with_retry`` re-reads and
re-applies a pure function on conflict.
"""
from __future__ import annotations

import re
import sqlite3
import threading
from typing import Callable

SCHEMA = """
CREATE TABLE IF NOT EXISTS products (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    price_cents INTEGER NOT NULL,
    stock INTEGER NOT NULL,
    version INTEGER NOT NULL DEFAULT 1
);
"""
EDITABLE = ("name", "price_cents", "stock")
# Exactly the tags etag_for() issues: strong (no W/), ASCII digits, no leading
# zeros. RFC 9110 13.1.1 requires strong comparison for If-Match, so anything
# the server would not have issued character for character is a mismatch.
CANONICAL_ETAG = re.compile(r'"v([1-9][0-9]*)"', re.ASCII)


class NotFound(Exception):
    """404: no such product."""


class PreconditionRequired(Exception):
    """428: the client did not send If-Match, so the write could be a lost update."""


class PreconditionFailed(Exception):
    """412: the record changed since the client read it."""

    def __init__(self, current_etag: str):
        super().__init__(f"record changed; current ETag is {current_etag}")
        self.current_etag = current_etag


class ConflictRetriesExhausted(Exception):
    """Too many concurrent writers; surface as 409/503 to the caller."""


def etag_for(version: int) -> str:
    return f'"v{version}"'


def parse_etag(value: str) -> int:
    """Parse '"v7"' into 7 using strong comparison. Raises ValueError otherwise.

    Weak tags (W/"v7") and non-canonical spellings ("v07", "v 7", "v1_0") are
    rejected: int() would accept them and let them match a real version.
    """
    match = CANONICAL_ETAG.fullmatch(value.strip())
    if match is None:
        raise ValueError(f"not a strong ETag issued by this server: {value!r}")
    return int(match.group(1))


class ProductStore:
    """Thin data-access layer. One lock serialises statements on the shared
    sqlite connection; it does NOT make read-modify-write atomic, which is the
    point: the race lives between the client's GET and its PUT."""

    def __init__(self, conn: sqlite3.Connection | None = None):
        self.conn = conn or sqlite3.connect(":memory:", check_same_thread=False)
        self.conn.executescript(SCHEMA)
        self._lock = threading.Lock()

    def create(self, pid: str, name: str, price_cents: int, stock: int) -> str:
        with self._lock, self.conn:
            self.conn.execute("INSERT INTO products (id, name, price_cents, stock) VALUES (?,?,?,?)",
                              (pid, name, price_cents, stock))
        return etag_for(1)

    def get(self, pid: str) -> tuple[dict, str]:
        with self._lock:
            row = self.conn.execute(
                "SELECT name, price_cents, stock, version FROM products WHERE id = ?", (pid,)).fetchone()
        if row is None:
            raise NotFound(pid)
        name, price, stock, version = row
        return {"id": pid, "name": name, "price_cents": price, "stock": stock}, etag_for(version)

    # --- the original, buggy handler -------------------------------------
    def put_unconditional(self, pid: str, body: dict) -> None:
        """Last write wins. Kept only to reproduce the incident in tests."""
        fields = {k: body[k] for k in EDITABLE}
        with self._lock, self.conn:
            cur = self.conn.execute(
                "UPDATE products SET name=?, price_cents=?, stock=?, version=version+1 WHERE id=?",
                (fields["name"], fields["price_cents"], fields["stock"], pid))
        if cur.rowcount == 0:
            raise NotFound(pid)

    # --- the fix ---------------------------------------------------------
    def put(self, pid: str, body: dict, if_match: str | None) -> str:
        """Full replace guarded by If-Match. Returns the new ETag."""
        if not if_match:
            raise PreconditionRequired("If-Match header is required for updates")
        try:
            expected = parse_etag(if_match)
        except ValueError:
            raise PreconditionFailed(self.get(pid)[1]) from None
        missing = [k for k in EDITABLE if k not in body]
        if missing:
            raise ValueError(f"missing fields: {missing}")
        with self._lock, self.conn:
            cur = self.conn.execute(
                "UPDATE products SET name=?, price_cents=?, stock=?, version=version+1 "
                "WHERE id=? AND version=?",
                (body["name"], body["price_cents"], body["stock"], pid, expected))
            if cur.rowcount == 1:
                return etag_for(expected + 1)
            row = self.conn.execute("SELECT version FROM products WHERE id=?", (pid,)).fetchone()
        if row is None:
            raise NotFound(pid)
        raise PreconditionFailed(etag_for(row[0]))

    def update_with_retry(self, pid: str, change: Callable[[dict], dict],
                          max_attempts: int = 10) -> dict:
        """Read, apply a *pure* ``change`` function, conditionally write; retry on 412.

        ``change`` must be safe to call several times (it may run once per attempt).
        """
        for _ in range(max_attempts):
            record, etag = self.get(pid)
            new = change(dict(record))
            try:
                self.put(pid, new, if_match=etag)
                return new
            except PreconditionFailed:
                continue
        raise ConflictRetriesExhausted(f"{pid}: gave up after {max_attempts} attempts")
