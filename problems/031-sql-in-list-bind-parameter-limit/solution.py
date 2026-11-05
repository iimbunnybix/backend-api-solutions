"""Bulk lookup that keeps working when the caller sends thousands of IDs.

The support console's "load selected customers" endpoint built one query,
``SELECT ... WHERE id IN (?, ?, ...)``, with one bind parameter per selected
ID. Databases cap the number of bind parameters in one statement (SQLite's
default was 999 before 3.32.0), so selecting 1,000+ customers made the
database reject the query and the endpoint returned HTTP 500.

The fix splits the ID list into chunks that stay under the parameter limit,
runs one query per chunk, removes duplicate IDs first, and returns the rows in
the order the caller asked for them.
"""
from __future__ import annotations

import sqlite3
from typing import Iterator, Sequence

# The production database was built with SQLite's historical default limit.
LEGACY_SQLITE_PARAMETER_LIMIT = 999

# Leave room for parameters other than the IDs (for example a tenant filter).
RESERVED_PARAMETERS = 1

# Hard cap on IDs per request, so one request cannot run unbounded queries.
MAX_IDS_PER_REQUEST = 10_000

Customer = tuple[int, str]


class TooManyIds(ValueError):
    """Raised when a request asks for more IDs than the API allows (HTTP 400)."""


def create_customer_table(connection: sqlite3.Connection, count: int, tenant_id: int = 1) -> None:
    """Create and fill a customers table with ``count`` rows for one tenant."""
    connection.execute("CREATE TABLE customers (id INTEGER PRIMARY KEY, tenant_id INTEGER, name TEXT)")
    rows = []
    for customer_id in range(1, count + 1):
        rows.append((customer_id, tenant_id, f"customer-{customer_id}"))
    connection.executemany("INSERT INTO customers VALUES (?, ?, ?)", rows)


def placeholders(count: int) -> str:
    """Return ``?, ?, ?`` with ``count`` question marks."""
    return ", ".join(["?"] * count)


def naive_load_customers(connection: sqlite3.Connection, tenant_id: int,
                         customer_ids: Sequence[int]) -> list[Customer]:
    """Original version: one bind parameter per ID in a single statement.

    Fails with ``sqlite3.OperationalError: too many SQL variables`` once the
    ID list plus the tenant parameter exceeds the database's limit.
    """
    sql = (f"SELECT id, name FROM customers WHERE tenant_id = ? "
           f"AND id IN ({placeholders(len(customer_ids))})")
    return connection.execute(sql, [tenant_id, *customer_ids]).fetchall()


def parameter_limit(connection: sqlite3.Connection) -> int:
    """Read the live bind-parameter limit from the connection."""
    return connection.getlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER)


def unique_in_order(customer_ids: Sequence[int]) -> list[int]:
    """Drop repeated IDs but keep the first-seen order.

    UI selections often contain the same ID twice; sending it twice wastes
    parameters and would return the row twice to the caller.
    """
    seen: set[int] = set()
    unique_ids: list[int] = []
    for customer_id in customer_ids:
        if customer_id not in seen:
            seen.add(customer_id)
            unique_ids.append(customer_id)
    return unique_ids


def chunks(items: list[int], chunk_size: int) -> Iterator[list[int]]:
    """Yield consecutive slices of ``items`` with at most ``chunk_size`` items."""
    if chunk_size < 1:
        raise ValueError("chunk_size must be at least 1")
    for start in range(0, len(items), chunk_size):
        yield items[start:start + chunk_size]


def load_customers(connection: sqlite3.Connection, tenant_id: int,
                   customer_ids: Sequence[int]) -> list[Customer]:
    """Fixed version: chunked queries, deduplicated IDs, caller's order kept.

    IDs that do not exist (or belong to another tenant) are simply absent from
    the result, the same as with the single-query version.
    """
    if len(customer_ids) > MAX_IDS_PER_REQUEST:
        raise TooManyIds(f"at most {MAX_IDS_PER_REQUEST} ids per request")
    unique_ids = unique_in_order(customer_ids)
    # Each chunk shares the statement with the tenant_id parameter.
    chunk_size = parameter_limit(connection) - RESERVED_PARAMETERS
    found: dict[int, str] = {}
    for id_chunk in chunks(unique_ids, chunk_size):
        sql = (f"SELECT id, name FROM customers WHERE tenant_id = ? "
               f"AND id IN ({placeholders(len(id_chunk))})")
        for customer_id, name in connection.execute(sql, [tenant_id, *id_chunk]):
            found[customer_id] = name
    # SQL returns each chunk in index order; rebuild the order the caller asked for.
    ordered: list[Customer] = []
    for customer_id in unique_ids:
        if customer_id in found:
            ordered.append((customer_id, found[customer_id]))
    return ordered


def queries_needed(id_count: int, limit: int) -> int:
    """How many chunked queries ``load_customers`` runs for ``id_count`` unique IDs."""
    chunk_size = limit - RESERVED_PARAMETERS
    return -(-id_count // chunk_size)  # ceiling division
