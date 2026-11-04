"""Offset pagination vs keyset (cursor) pagination for a feed sorted newest-first.

``OFFSET`` pagination re-counts rows from the top on every page, so rows
inserted (or deleted) while a client is paging shift everything and the
client sees duplicates (or silently skips rows). Keyset pagination remembers
the *position* of the last row, ``(created_at, id)``, and asks for rows
strictly after it, so concurrent writes at the head cannot shift the window.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import sqlite3
from dataclasses import dataclass


class InvalidCursor(ValueError):
    """Raised for tampered, malformed or foreign cursors (map to HTTP 400)."""


def create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE events (id INTEGER PRIMARY KEY, created_at INTEGER NOT NULL,
                             body TEXT NOT NULL);
        -- composite index matching ORDER BY so each page is an index range scan
        CREATE INDEX idx_events_feed ON events(created_at DESC, id DESC);
        """
    )


def page_offset(conn: sqlite3.Connection, page: int, size: int) -> list[tuple]:
    """The original implementation: ``?page=N`` → ``OFFSET N*size``."""
    return conn.execute(
        "SELECT id, created_at, body FROM events ORDER BY created_at DESC, id DESC "
        "LIMIT ? OFFSET ?", (size, page * size)).fetchall()


@dataclass
class Page:
    rows: list[tuple]
    next_cursor: str | None


class CursorCodec:
    """Opaque, signed cursors so clients can't forge positions or swap sort orders.

    The cursor is ``base64(json) + "." + hmac`` — not encrypted (it only holds
    a timestamp and id), but integrity-protected.
    """

    def __init__(self, key: bytes, version: int = 1):
        if len(key) < 16:
            raise ValueError("cursor signing key must be at least 16 bytes")
        self._key = key
        self._version = version

    def _sig(self, payload: bytes) -> str:
        return hmac.new(self._key, payload, hashlib.sha256).hexdigest()[:32]

    def encode(self, created_at: int, row_id: int) -> str:
        payload = base64.urlsafe_b64encode(
            json.dumps({"v": self._version, "c": created_at, "i": row_id},
                       separators=(",", ":")).encode()).decode().rstrip("=")
        return f"{payload}.{self._sig(payload.encode())}"

    def decode(self, cursor: str) -> tuple[int, int]:
        try:
            payload, sig = cursor.rsplit(".", 1)
        except ValueError as e:
            raise InvalidCursor("malformed cursor") from e
        if not hmac.compare_digest(sig, self._sig(payload.encode())):
            raise InvalidCursor("cursor signature mismatch")
        try:
            data = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
            if data["v"] != self._version:
                raise InvalidCursor("cursor from another API version")
            c, i = data["c"], data["i"]
        except InvalidCursor:
            raise
        except Exception as e:  # noqa: BLE001 - any decode failure is a bad cursor
            raise InvalidCursor("undecodable cursor") from e
        if not (isinstance(c, int) and isinstance(i, int)):
            raise InvalidCursor("bad cursor field types")
        return c, i


def page_keyset(conn: sqlite3.Connection, codec: CursorCodec, size: int,
                cursor: str | None = None, max_size: int = 100) -> Page:
    """Return one page and an opaque cursor for the next one (None at the end).

    The row-value comparison ``(created_at, id) < (?, ?)`` uses ``id`` as a
    tie-breaker so rows sharing a timestamp are neither duplicated nor skipped.
    Fetches ``size + 1`` rows to know whether another page exists without COUNT(*).
    """
    if not 1 <= size <= max_size:
        raise ValueError(f"size must be between 1 and {max_size}")
    if cursor is None:
        rows = conn.execute(
            "SELECT id, created_at, body FROM events ORDER BY created_at DESC, id DESC "
            "LIMIT ?", (size + 1,)).fetchall()
    else:
        c, i = codec.decode(cursor)
        rows = conn.execute(
            "SELECT id, created_at, body FROM events WHERE (created_at, id) < (?, ?) "
            "ORDER BY created_at DESC, id DESC LIMIT ?", (c, i, size + 1)).fetchall()
    has_more = len(rows) > size
    rows = rows[:size]
    nxt = codec.encode(rows[-1][1], rows[-1][0]) if has_more else None
    return Page(rows, nxt)
