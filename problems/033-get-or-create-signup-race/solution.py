"""Get-or-create that stays correct when the same user signs up twice at once.

The "Continue with Google" endpoint looked the user up by email and, if no
row existed, inserted one. The mobile app sent the call twice on a double tap
(and once more from a background prefetch), so two requests often both saw
"no user" and both inserted. Before the email column was unique this created
duplicate accounts; after a unique index was added, the second insert failed
with a unique-constraint error and the user saw an HTTP 500 on their very
first login.

The fix lets the database decide: insert with ``ON CONFLICT (email) DO
NOTHING`` and then re-read the row. Whichever request inserts first wins;
every other request reads that same row back, so all of them succeed and
return one account.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Callable

HTTP_OK = 200
HTTP_CREATED = 201
HTTP_INTERNAL_ERROR = 500

# A hook that runs between "check" and "insert". Tests use it to run another
# request at exactly the moment the race happens in production.
Hook = Callable[[], None]


@dataclass(frozen=True)
class User:
    id: int
    email: str
    name: str


def do_nothing() -> None:
    """Default hook: no other request runs in the gap."""


def open_database() -> sqlite3.Connection:
    """In-memory database in autocommit mode, like one statement per request step."""
    return sqlite3.connect(":memory:", isolation_level=None)


def create_users_table(connection: sqlite3.Connection, unique_email: bool) -> None:
    """Create the users table, with or without a unique constraint on email.

    The legacy schema had no constraint; the unique index was added after the
    first duplicate accounts were found.
    """
    email_column = "email TEXT NOT NULL UNIQUE" if unique_email else "email TEXT NOT NULL"
    connection.execute(
        f"CREATE TABLE users (id INTEGER PRIMARY KEY, {email_column}, name TEXT NOT NULL)")


def find_user(connection: sqlite3.Connection, email: str) -> User | None:
    """Return the oldest user with this email, or None."""
    row = connection.execute(
        "SELECT id, email, name FROM users WHERE email = ? ORDER BY id LIMIT 1", (email,)
    ).fetchone()
    if row is None:
        return None
    return User(id=row[0], email=row[1], name=row[2])


def count_users_with_email(connection: sqlite3.Connection, email: str) -> int:
    """Number of rows for one email (more than 1 means duplicate accounts)."""
    return connection.execute("SELECT COUNT(*) FROM users WHERE email = ?", (email,)).fetchone()[0]


def naive_get_or_create(connection: sqlite3.Connection, email: str, name: str,
                        between_check_and_insert: Hook = do_nothing) -> tuple[User, bool]:
    """Original version: check, then insert. Returns (user, created).

    Two requests can both pass the check before either inserts. Without a
    unique index both insert (duplicate rows); with one, the second raises
    ``sqlite3.IntegrityError``.
    """
    existing = find_user(connection, email)
    if existing is not None:
        return existing, False
    between_check_and_insert()
    cursor = connection.execute("INSERT INTO users (email, name) VALUES (?, ?)", (email, name))
    return User(id=cursor.lastrowid, email=email, name=name), True


def get_or_create(connection: sqlite3.Connection, email: str, name: str,
                  between_check_and_insert: Hook = do_nothing) -> tuple[User, bool]:
    """Fixed version: insert-if-absent, then re-read. Returns (user, created).

    Requires a unique constraint on email; the constraint is what makes the
    database, not the application, pick a single winner.
    """
    # Fast path: almost every call is a returning user, so read first and
    # avoid a write when the row already exists.
    existing = find_user(connection, email)
    if existing is not None:
        return existing, False
    between_check_and_insert()
    # DO NOTHING turns "someone else inserted first" into a no-op instead of
    # an error. rowcount tells us whether this request was the one that won.
    cursor = connection.execute(
        "INSERT INTO users (email, name) VALUES (?, ?) ON CONFLICT (email) DO NOTHING",
        (email, name))
    created = cursor.rowcount == 1
    # Re-read so the loser returns the winner's row (its id and its name).
    user = find_user(connection, email)
    if user is None:
        raise LookupError(f"user {email!r} vanished right after insert")
    return user, created


GetOrCreate = Callable[[sqlite3.Connection, str, str, Hook], tuple[User, bool]]


def handle_login(connection: sqlite3.Connection, email: str, name: str,
                 implementation: GetOrCreate, between_check_and_insert: Hook = do_nothing) -> int:
    """The login endpoint: return the HTTP status the user would see.

    An unhandled database error becomes a 500, exactly what the web framework did.
    """
    try:
        _, created = implementation(connection, email, name, between_check_and_insert)
    except sqlite3.Error:
        return HTTP_INTERNAL_ERROR
    if created:
        return HTTP_CREATED
    return HTTP_OK


def double_tap_login(connection: sqlite3.Connection, email: str, name: str,
                     implementation: GetOrCreate) -> list[int]:
    """Simulate a double tap: request B runs entirely inside request A's check-insert gap.

    Returns the statuses in completion order: [B, A].
    """
    statuses: list[int] = []

    def second_request_runs_now() -> None:
        statuses.append(handle_login(connection, email, name, implementation))

    statuses.append(handle_login(connection, email, name, implementation, second_request_runs_now))
    return statuses
