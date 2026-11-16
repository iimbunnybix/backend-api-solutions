"""Bulk delete with an empty ``?ids=`` filter deleted every row in the table.

The notes API supports ``DELETE /notes?ids=4,7,9``. The handler built its SQL
by appending ``WHERE id IN (...)`` only when the id list was non-empty, so a
request with ``?ids=`` (a client bug: an empty selection) ran a bare
``DELETE FROM notes`` and removed every user's notes. This module treats an
empty or invalid filter as a bad request, always scopes the delete to the
caller's own rows, checks the affected row count inside the transaction, and
moves "delete everything" to a separate call that needs an explicit
confirmation.
"""
from __future__ import annotations

import sqlite3

# The confirmation text a client must send to really delete all its notes.
DELETE_ALL_CONFIRMATION = "delete all my notes"
# Keeps one request's statement well under database bind-parameter limits
# (SQLite allowed only 999 before 3.32) and bounds the damage of one call.
MAX_IDS_PER_REQUEST = 500


class BadRequestError(ValueError):
    """The request is invalid; maps to HTTP 400."""


def create_schema(connection: sqlite3.Connection) -> None:
    """Create the notes table used by the endpoint."""
    connection.execute(
        "CREATE TABLE notes (id INTEGER PRIMARY KEY, owner_id INTEGER NOT NULL, body TEXT)"
    )


def naive_delete_notes(connection: sqlite3.Connection, ids_param: str) -> int:
    """Original handler: the WHERE clause disappears when the id list is empty."""
    ids = [part for part in ids_param.split(",") if part]
    sql = "DELETE FROM notes"
    if ids:
        placeholders = ",".join("?" for _ in ids)
        sql += f" WHERE id IN ({placeholders})"
    with connection:
        cursor = connection.execute(sql, [int(note_id) for note_id in ids])
    return cursor.rowcount


def parse_ids_param(ids_param: str) -> list[int]:
    """Turn "4,7,9" into [4, 7, 9]; an empty or malformed list is a 400."""
    parts = [part.strip() for part in ids_param.split(",")]
    if parts == [""]:
        raise BadRequestError("ids must list at least one note id")
    ids: list[int] = []
    for part in parts:
        # A stray comma ("4,,7") or a word is a client bug, not "no filter".
        if not part.isdigit():
            raise BadRequestError(f"invalid note id {part!r}")
        ids.append(int(part))
    unique_ids = sorted(set(ids))
    if len(unique_ids) > MAX_IDS_PER_REQUEST:
        raise BadRequestError(f"at most {MAX_IDS_PER_REQUEST} ids per request")
    return unique_ids


def build_delete_by_ids(owner_id: int, ids: list[int]) -> tuple[str, list[int]]:
    """Build a DELETE that can only ever touch the listed ids of one owner.

    The WHERE clause is not optional: there is no code path that returns SQL
    without it, so an empty list cannot widen the delete.
    """
    if not ids:
        raise BadRequestError("refusing to build a delete with no ids")
    placeholders = ",".join("?" for _ in ids)
    sql = f"DELETE FROM notes WHERE owner_id = ? AND id IN ({placeholders})"
    return sql, [owner_id, *ids]


def delete_notes(connection: sqlite3.Connection, owner_id: int, ids_param: str) -> int:
    """Fixed handler for DELETE /notes?ids=...; returns how many notes were deleted."""
    ids = parse_ids_param(ids_param)
    sql, params = build_delete_by_ids(owner_id, ids)
    with connection:
        cursor = connection.execute(sql, params)
        # Defence in depth: if a future change breaks the WHERE clause, more
        # rows than requested would be deleted. Raising here rolls it back.
        if cursor.rowcount > len(ids):
            raise RuntimeError(
                f"delete matched {cursor.rowcount} rows for {len(ids)} ids; rolled back"
            )
    return cursor.rowcount


def delete_all_notes(connection: sqlite3.Connection, owner_id: int, confirmation: str) -> int:
    """Separate, explicit "delete all": only the caller's notes, only when confirmed."""
    if confirmation != DELETE_ALL_CONFIRMATION:
        raise BadRequestError(f"send confirmation={DELETE_ALL_CONFIRMATION!r} to delete all notes")
    with connection:
        cursor = connection.execute("DELETE FROM notes WHERE owner_id = ?", [owner_id])
    return cursor.rowcount
