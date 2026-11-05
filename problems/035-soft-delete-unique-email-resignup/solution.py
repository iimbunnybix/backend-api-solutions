"""Let users who deleted their account sign up again, without allowing duplicates.

Accounts are soft-deleted: ``deleted_at`` is set and the row is kept for the
retention period. The original schema had ``UNIQUE (email)`` over the whole
table, so a deleted row still "owned" its email and re-signup failed. The
first hot fix dropped the constraint and checked in code, which let two
concurrent sign-ups create two live accounts with the same email. Listings
also read the raw table, so deleted users kept showing up.

The fix enforces uniqueness only among live rows with a partial unique index
(``... WHERE deleted_at IS NULL``), so the database itself rejects a second
live account, and every listing excludes deleted rows unless asked not to.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass

TABLE_WITHOUT_CONSTRAINTS = """
    CREATE TABLE accounts (
        id INTEGER PRIMARY KEY,
        email TEXT NOT NULL,
        name TEXT NOT NULL,
        deleted_at TEXT            -- NULL means live; a timestamp means soft-deleted
    )"""

# Original: the unique constraint covers deleted rows too.
ORIGINAL_UNIQUE_INDEX = "CREATE UNIQUE INDEX accounts_email ON accounts (email)"

# Fix: unique only among live rows. Deleted rows may share an email with
# each other and with one live row.
LIVE_ONLY_UNIQUE_INDEX = (
    "CREATE UNIQUE INDEX accounts_live_email ON accounts (email) WHERE deleted_at IS NULL")


class EmailTaken(Exception):
    """A live account already uses this email."""


@dataclass(frozen=True)
class Account:
    id: int
    email: str
    name: str
    deleted_at: str | None


def _open_store(unique_index_sql: str | None) -> sqlite3.Connection:
    """In-memory accounts table with the given unique index (or none)."""
    connection = sqlite3.connect(":memory:")
    connection.execute(TABLE_WITHOUT_CONSTRAINTS)
    if unique_index_sql is not None:
        connection.execute(unique_index_sql)
    return connection


def open_original_store() -> sqlite3.Connection:
    """Schema before the incident: email unique across all rows, deleted or not."""
    return _open_store(ORIGINAL_UNIQUE_INDEX)


def open_constraint_dropped_store() -> sqlite3.Connection:
    """First hot fix: no unique index at all, uniqueness checked only in code."""
    return _open_store(None)


def open_fixed_store() -> sqlite3.Connection:
    """Fixed schema: email unique among live rows only (partial unique index)."""
    return _open_store(LIVE_ONLY_UNIQUE_INDEX)


def live_email_exists(connection: sqlite3.Connection, email: str) -> bool:
    """The application-level check a sign-up request runs first."""
    row = connection.execute(
        "SELECT 1 FROM accounts WHERE email = ? AND deleted_at IS NULL", (email,)).fetchone()
    return row is not None


def insert_account(connection: sqlite3.Connection, email: str, name: str) -> int:
    """Insert a live account. The database index is the final word on uniqueness."""
    try:
        with connection:
            cursor = connection.execute(
                "INSERT INTO accounts (email, name) VALUES (?, ?)", (email, name))
    except sqlite3.IntegrityError as exc:
        raise EmailTaken(email) from exc
    return cursor.lastrowid


def sign_up(connection: sqlite3.Connection, email: str, name: str) -> int:
    """Create an account; returns its id or raises EmailTaken.

    The pre-check gives a friendly error in the common case. It cannot stop
    two concurrent requests, which is why ``insert_account`` relies on the index.
    """
    if live_email_exists(connection, email):
        raise EmailTaken(email)
    return insert_account(connection, email, name)


def soft_delete(connection: sqlite3.Connection, account_id: int, now: str) -> None:
    """Mark an account deleted; the row is kept for the retention period."""
    with connection:
        connection.execute(
            "UPDATE accounts SET deleted_at = ? WHERE id = ? AND deleted_at IS NULL",
            (now, account_id))


def restore(connection: sqlite3.Connection, account_id: int) -> None:
    """Undo a soft delete. Fails if the email now belongs to a newer live account."""
    try:
        with connection:
            connection.execute(
                "UPDATE accounts SET deleted_at = NULL WHERE id = ?", (account_id,))
    except sqlite3.IntegrityError as exc:
        raise EmailTaken(f"account {account_id}: email is used by a live account") from exc


def _rows_to_accounts(rows: list[tuple]) -> list[Account]:
    """Convert raw rows (id, email, name, deleted_at) to Account objects."""
    return [Account(*row) for row in rows]


def naive_list_accounts(connection: sqlite3.Connection) -> list[Account]:
    """Original listing: reads the raw table, so deleted accounts leak into results."""
    rows = connection.execute(
        "SELECT id, email, name, deleted_at FROM accounts ORDER BY id").fetchall()
    return _rows_to_accounts(rows)


def list_accounts(connection: sqlite3.Connection,
                  include_deleted: bool = False) -> list[Account]:
    """Listing used by the API: live accounts only, unless an admin asks for all."""
    sql = "SELECT id, email, name, deleted_at FROM accounts"
    # Excluding deleted rows is the default so a new endpoint cannot forget it.
    if not include_deleted:
        sql += " WHERE deleted_at IS NULL"
    rows = connection.execute(sql + " ORDER BY id").fetchall()
    return _rows_to_accounts(rows)
