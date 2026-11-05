"""Customer search that treats "%" and "_" as literal characters.

The support tool's search box ran ``WHERE email LIKE ?`` with the user's text
wrapped in ``%...%``. The query was parameterised, so it was safe from SQL
injection, but LIKE gives "%" and "_" their own meaning *inside the value*.
Searching for "_" or "%" matched every customer (and scanned the whole table),
and searching for "50%_off" found unrelated rows.

The fix escapes the LIKE metacharacters in the user's text and tells the
database which escape character was used (``ESCAPE '\\'``), so the search text
is matched literally. Very short terms are rejected to stop accidental
full-table matches.
"""
from __future__ import annotations

import sqlite3

# The escape character must itself be escaped first, or a user typing "\"
# would escape our own escape characters.
LIKE_ESCAPE_CHAR = "\\"
LIKE_METACHARACTERS = ("%", "_")
MIN_SEARCH_LENGTH = 2
MAX_RESULTS = 50


class SearchTermTooShort(ValueError):
    """The search text is too short to give a useful, bounded result."""


def create_customer_db(emails: list[str]) -> sqlite3.Connection:
    """Build an in-memory customers table filled with the given emails."""
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE customers (id INTEGER PRIMARY KEY, email TEXT NOT NULL)")
    db.executemany("INSERT INTO customers (email) VALUES (?)", [(email,) for email in emails])
    return db


def naive_search(db: sqlite3.Connection, text: str) -> list[str]:
    """The original query: parameterised, but "%" and "_" act as wildcards."""
    pattern = f"%{text}%"
    rows = db.execute(
        "SELECT email FROM customers WHERE email LIKE ? ORDER BY id LIMIT ?",
        (pattern, MAX_RESULTS),
    ).fetchall()
    return [row[0] for row in rows]


def escape_like(text: str) -> str:
    """Escape LIKE metacharacters so ``text`` matches only itself."""
    escaped = text.replace(LIKE_ESCAPE_CHAR, LIKE_ESCAPE_CHAR + LIKE_ESCAPE_CHAR)
    for metacharacter in LIKE_METACHARACTERS:
        escaped = escaped.replace(metacharacter, LIKE_ESCAPE_CHAR + metacharacter)
    return escaped


def validate_search_text(text: str) -> str:
    """Strip whitespace and reject terms too short to search on."""
    cleaned = text.strip()
    if len(cleaned) < MIN_SEARCH_LENGTH:
        raise SearchTermTooShort(
            f"search text must be at least {MIN_SEARCH_LENGTH} characters"
        )
    return cleaned


def safe_search(db: sqlite3.Connection, text: str) -> list[str]:
    """Find customers whose email contains ``text`` literally."""
    cleaned = validate_search_text(text)
    pattern = f"%{escape_like(cleaned)}%"
    # ESCAPE tells the database which character marks a literal "%" or "_".
    # Without it, the backslashes we added would be matched as text.
    rows = db.execute(
        "SELECT email FROM customers WHERE email LIKE ? ESCAPE ? ORDER BY id LIMIT ?",
        (pattern, LIKE_ESCAPE_CHAR, MAX_RESULTS),
    ).fetchall()
    return [row[0] for row in rows]
