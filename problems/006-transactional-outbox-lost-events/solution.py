"""Transactional outbox: never lose (or invent) an "OrderPlaced" event.

The original bug was a *dual write*: the order service committed the order to
its database and then published an event to the message broker. If the
process crashed, or the broker call failed, between the two, the order existed
but no event was ever sent, so the warehouse never shipped it. Publishing
first was no better: a failed DB commit produced an event for an order that
did not exist.

Fix: write the event to an ``outbox`` table **in the same database
transaction** as the order. A separate relay reads unsent outbox rows in
order, publishes them, and marks them sent. Delivery becomes at-least-once,
so each event carries a stable id and consumers de-duplicate on it.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from typing import Callable, Protocol


class Broker(Protocol):
    def publish(self, topic: str, event_id: str, payload: dict) -> None: ...


SCHEMA = """
CREATE TABLE IF NOT EXISTS orders (
    id TEXT PRIMARY KEY, customer TEXT NOT NULL, total_cents INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS outbox (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT UNIQUE NOT NULL,
    topic TEXT NOT NULL,
    payload TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    sent INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_outbox_unsent ON outbox(sent, seq);
"""


def connect(path: str = ":memory:") -> sqlite3.Connection:
    db = sqlite3.connect(path, isolation_level=None)  # explicit BEGIN/COMMIT
    db.executescript(SCHEMA)
    return db


def _insert_order(db: sqlite3.Connection, customer: str, total_cents: int) -> str:
    if total_cents <= 0:
        raise ValueError("total must be positive")
    order_id = str(uuid.uuid4())
    db.execute("INSERT INTO orders VALUES (?, ?, ?)", (order_id, customer, total_cents))
    return order_id


# --- the original, broken dual write ------------------------------------
def place_order_dual_write(db, broker: Broker, customer: str, total_cents: int) -> str:
    """Commit, then publish. A crash or broker error in between loses the event."""
    db.execute("BEGIN")
    order_id = _insert_order(db, customer, total_cents)
    db.execute("COMMIT")
    broker.publish("orders", str(uuid.uuid4()),
                   {"order_id": order_id, "customer": customer, "total_cents": total_cents})
    return order_id


# --- the fix ------------------------------------------------------------
def place_order(db: sqlite3.Connection, customer: str, total_cents: int,
                before_commit: Callable[[], None] = lambda: None) -> str:
    """Insert the order and its event atomically. No broker call here.

    ``before_commit`` is a test hook to simulate a crash mid-transaction.
    """
    db.execute("BEGIN")
    try:
        order_id = _insert_order(db, customer, total_cents)
        payload = {"order_id": order_id, "customer": customer, "total_cents": total_cents}
        db.execute(
            "INSERT INTO outbox(event_id, topic, payload) VALUES (?, ?, ?)",
            (str(uuid.uuid4()), "orders", json.dumps(payload, sort_keys=True)),
        )
        before_commit()
        db.execute("COMMIT")
    except BaseException:
        db.execute("ROLLBACK")
        raise
    return order_id


class OutboxRelay:
    """Publishes unsent outbox rows in commit order.

    Run one active relay per outbox (e.g. behind a leader lease) so ordering
    holds. A row is marked sent only after the broker acknowledges it; if the
    relay dies after publishing but before marking, the row is re-sent on the
    next run, which is why consumers must de-duplicate by ``event_id``.
    """

    def __init__(self, db: sqlite3.Connection, broker: Broker, batch_size: int = 100):
        self.db, self.broker, self.batch_size = db, broker, batch_size

    def run_once(self) -> int:
        """Publish up to ``batch_size`` events; stop at the first failure.

        Stopping (rather than skipping) keeps per-outbox ordering: a later
        event is never published before an earlier one. Returns count sent.
        """
        rows = self.db.execute(
            "SELECT seq, event_id, topic, payload FROM outbox WHERE sent = 0 "
            "ORDER BY seq LIMIT ?", (self.batch_size,)
        ).fetchall()
        sent = 0
        for seq, event_id, topic, payload in rows:
            try:
                self.broker.publish(topic, event_id, json.loads(payload))
            except Exception:
                self.db.execute("UPDATE outbox SET attempts = attempts + 1 WHERE seq = ?", (seq,))
                break
            self.db.execute("UPDATE outbox SET sent = 1, attempts = attempts + 1 WHERE seq = ?", (seq,))
            sent += 1
        return sent

    def backlog(self) -> int:
        """Unsent events: alert if this keeps growing (broker down / relay dead)."""
        return self.db.execute("SELECT COUNT(*) FROM outbox WHERE sent = 0").fetchone()[0]

    def purge_sent(self) -> int:
        """Delete published rows so the table stays small."""
        return self.db.execute("DELETE FROM outbox WHERE sent = 1").rowcount


class DedupingConsumer:
    """Consumer side of at-least-once: process each ``event_id`` once."""

    def __init__(self) -> None:
        self.seen: set[str] = set()
        self.shipped: list[str] = []

    def handle(self, event_id: str, payload: dict) -> bool:
        if event_id in self.seen:
            return False
        # Side effect first: if it raises, the id stays unseen so the broker's
        # redelivery is processed instead of being dropped as a "duplicate".
        self.shipped.append(payload["order_id"])
        self.seen.add(event_id)            # in production: same txn as the side effect
        return True
