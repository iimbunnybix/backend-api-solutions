"""Order listing API: N+1 query version vs batched (DataLoader-style) version.

The schema is deliberately tiny so the query pattern is the focus:

    customers(id, name)
    orders(id, customer_id, total_cents, created_at)
    order_items(id, order_id, sku, qty)

``QueryCounter`` wraps a sqlite3 connection and counts statements so tests
can assert on the *number of round trips*, which is what hurts in production
(each round trip to a networked database costs ~0.5-2 ms before any work).
"""
from __future__ import annotations

import sqlite3
from collections import defaultdict
from typing import Iterable, Sequence

# SQLite's default SQLITE_MAX_VARIABLE_NUMBER is 999 on older builds and 32766
# on newer ones; Postgres allows 65535 bind params. Chunking keeps IN lists bounded.
MAX_IN_PARAMS = 500


class QueryCounter:
    """Thin wrapper around a sqlite3 connection that counts executed statements."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self.count = 0

    def execute(self, sql: str, params: Sequence = ()) -> list[tuple]:
        self.count += 1
        return self.conn.execute(sql, params).fetchall()


def create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE customers (id INTEGER PRIMARY KEY, name TEXT NOT NULL);
        CREATE TABLE orders (id INTEGER PRIMARY KEY, customer_id INTEGER NOT NULL,
                             total_cents INTEGER NOT NULL, created_at INTEGER NOT NULL);
        CREATE TABLE order_items (id INTEGER PRIMARY KEY, order_id INTEGER NOT NULL,
                                  sku TEXT NOT NULL, qty INTEGER NOT NULL);
        CREATE INDEX idx_items_order ON order_items(order_id);
        """
    )


def _page_of_orders(db: QueryCounter, limit: int) -> list[tuple]:
    return db.execute(
        "SELECT id, customer_id, total_cents, created_at FROM orders "
        "ORDER BY created_at DESC, id DESC LIMIT ?", (limit,))


def list_orders_naive(db: QueryCounter, limit: int = 50) -> list[dict]:
    """The original endpoint: 1 query for orders + 2 per order (customer, items).

    This is what an ORM's lazy-loaded relationships produce when a serializer
    touches ``order.customer.name`` and ``order.items`` inside a loop.
    """
    out = []
    for oid, cid, total, created in _page_of_orders(db, limit):
        (name,) = db.execute("SELECT name FROM customers WHERE id = ?", (cid,))[0]
        items = db.execute(
            "SELECT sku, qty FROM order_items WHERE order_id = ? ORDER BY id", (oid,))
        out.append({"id": oid, "customer": name, "total_cents": total, "created_at": created,
                    "items": [{"sku": s, "qty": q} for s, q in items]})
    return out


def _chunks(ids: Sequence[int], size: int) -> Iterable[Sequence[int]]:
    for i in range(0, len(ids), size):
        yield ids[i:i + size]


def load_many(db: QueryCounter, sql_template: str, ids: Iterable[int],
              chunk: int = MAX_IN_PARAMS) -> list[tuple]:
    """Run ``sql_template`` (containing ``{in}``) for distinct ids in bounded IN-chunks."""
    unique = sorted(set(ids))
    rows: list[tuple] = []
    for part in _chunks(unique, chunk):
        placeholders = ",".join("?" * len(part))
        rows.extend(db.execute(sql_template.format(**{"in": placeholders}), tuple(part)))
    return rows


def list_orders_batched(db: QueryCounter, limit: int = 50,
                        chunk: int = MAX_IN_PARAMS) -> list[dict]:
    """Same response, constant number of queries: 1 page + 1 customers + 1 items
    (per ``chunk`` ids). Results are stitched in Python via dict lookups."""
    orders = _page_of_orders(db, limit)
    if not orders:
        return []
    customers = dict(load_many(
        db, "SELECT id, name FROM customers WHERE id IN ({in})",
        (o[1] for o in orders), chunk))
    items_by_order: dict[int, list[dict]] = defaultdict(list)
    for order_id, sku, qty in load_many(
            db, "SELECT order_id, sku, qty FROM order_items WHERE order_id IN ({in}) "
                "ORDER BY order_id, id", (o[0] for o in orders), chunk):
        items_by_order[order_id].append({"sku": sku, "qty": qty})
    return [{"id": oid, "customer": customers.get(cid), "total_cents": total,
             "created_at": created, "items": items_by_order.get(oid, [])}
            for oid, cid, total, created in orders]
