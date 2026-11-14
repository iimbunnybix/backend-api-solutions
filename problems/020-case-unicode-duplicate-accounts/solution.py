"""Sign-up that treats "Alice", "alice" and "ａｌｉｃｅ" as the same username.

The original sign-up API enforced uniqueness with a UNIQUE index on the raw
username and email strings. Users who typed a different letter case, or whose
phone keyboard produced full-width or composed Unicode characters, got a second
account; support then saw duplicate accounts, split order histories and
impersonation ("Admin" next to "admin"). This version stores a canonical key
(Unicode NFKC normalisation + case folding) next to the display value and puts
the UNIQUE index on the canonical key, so the database itself rejects lookalikes.
"""
from __future__ import annotations

import sqlite3
import unicodedata

MAX_USERNAME_LENGTH = 32


class DuplicateAccount(Exception):
    """Raised when the canonical username or email is already taken."""


def canonical_identifier(raw: str) -> str:
    """Return the form used for uniqueness checks and logins.

    NFKC folds compatibility characters (full-width letters, ligatures like
    "ﬁ") and composes accents into one code point, so visually identical
    strings get identical code points. casefold() is a stronger lower() that
    also maps characters like "ß" to "ss". NFKC runs again after casefold
    because folding can produce sequences that need re-normalising.
    """
    stripped = raw.strip()
    normalised = unicodedata.normalize("NFKC", stripped)
    folded = normalised.casefold()
    return unicodedata.normalize("NFKC", folded)


def create_naive_schema(conn: sqlite3.Connection) -> None:
    """Original schema: uniqueness on the exact bytes the user typed."""
    conn.execute(
        "CREATE TABLE users (id INTEGER PRIMARY KEY,"
        " username TEXT NOT NULL UNIQUE, email TEXT NOT NULL UNIQUE)"
    )


def naive_sign_up(conn: sqlite3.Connection, username: str, email: str) -> int:
    """Insert the user as typed; only byte-identical duplicates are rejected."""
    try:
        cursor = conn.execute(
            "INSERT INTO users (username, email) VALUES (?, ?)", (username, email))
    except sqlite3.IntegrityError as error:
        raise DuplicateAccount(str(error)) from error
    return int(cursor.lastrowid)


def create_schema(conn: sqlite3.Connection) -> None:
    """Fixed schema: keep what the user typed for display, enforce uniqueness on canonical keys."""
    conn.execute(
        "CREATE TABLE users (id INTEGER PRIMARY KEY,"
        " username_display TEXT NOT NULL, username_key TEXT NOT NULL UNIQUE,"
        " email_display TEXT NOT NULL, email_key TEXT NOT NULL UNIQUE)"
    )


def validate_username(username: str) -> None:
    """Reject empty, over-long or control-character usernames after normalising."""
    key = canonical_identifier(username)
    if not key:
        raise ValueError("username must not be empty")
    if len(key) > MAX_USERNAME_LENGTH:
        raise ValueError(f"username longer than {MAX_USERNAME_LENGTH} characters")
    for character in key:
        # Category "C*" covers control, format (zero-width) and unassigned
        # characters, which make two names look identical but differ.
        if unicodedata.category(character).startswith("C"):
            raise ValueError("username contains invisible or control characters")


def validate_email(email: str) -> None:
    """Reject blank, malformed or control-character emails after normalising.

    Without this, a zero-width character inside an email creates a lookalike
    second account, and a blank email is stored as '' and then blocks every
    later blank sign-up with a misleading "already registered".
    """
    key = canonical_identifier(email)
    if not key:
        raise ValueError("email must not be empty")
    for character in key:
        if unicodedata.category(character).startswith("C"):
            raise ValueError("email contains invisible or control characters")
    # Exactly one "@" with something on both sides; deeper checks belong to
    # the confirmation email, not to this uniqueness guard.
    local_part, at_sign, domain = key.partition("@")
    if not at_sign or not local_part or not domain or "@" in domain:
        raise ValueError("email must look like name@domain")


def sign_up(conn: sqlite3.Connection, username: str, email: str) -> int:
    """Create an account unless the canonical username or email already exists.

    The check is the UNIQUE index itself, not a SELECT-then-INSERT, so two
    concurrent sign-ups for "Bob" and "bob" cannot both succeed.
    """
    validate_username(username)
    validate_email(email)
    try:
        cursor = conn.execute(
            "INSERT INTO users (username_display, username_key, email_display, email_key)"
            " VALUES (?, ?, ?, ?)",
            (username.strip(), canonical_identifier(username),
             email.strip(), canonical_identifier(email)),
        )
    except sqlite3.IntegrityError as error:
        raise DuplicateAccount("username or email already registered") from error
    return int(cursor.lastrowid)


def find_user_id(conn: sqlite3.Connection, username: str) -> int | None:
    """Look a user up at login by canonical key, so any spelling of the name works."""
    row = conn.execute(
        "SELECT id FROM users WHERE username_key = ?", (canonical_identifier(username),)
    ).fetchone()
    if row is None:
        return None
    return int(row[0])
