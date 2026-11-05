"""Checkout that does not hold a database transaction open while calling the payment provider.

The original checkout handler opened a transaction, inserted the order, called
the external payment provider over HTTP, then marked the order paid and
committed. The connection (and the order-table write lock) stayed checked out
for the whole provider call. When the provider slowed from 0.3 s to 3 s, every
checkout held a pooled connection ten times longer, the pool ran dry, and even
requests that never touch payments failed with "pool exhausted".

The fix splits the work into two short transactions around the HTTP call: first
commit the order as PENDING, release the connection, call the provider with the
order id as idempotency key, then record PAID or FAILED in a second transaction.
A reconciler finds orders stuck in PENDING (crash between charge and record).
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from typing import Callable, Iterator, Optional

DB_WORK_SECONDS = 0.005  # simulated time for one short transaction's SQL
STATUS_PENDING = "PENDING"
STATUS_PAID = "PAID"
STATUS_FAILED = "FAILED"
SCHEMA = "CREATE TABLE orders (id TEXT PRIMARY KEY, amount_cents INTEGER, status TEXT, created_at REAL)"


class FakeClock:
    """Manually advanced clock, in seconds."""

    def __init__(self) -> None:
        self.now = 0.0

    def advance(self, seconds: float) -> None:
        self.now += seconds


class PoolExhausted(Exception):
    """No free connection: the API would answer 503 after its acquire timeout."""


class PaymentDeclined(Exception):
    """The provider refused the charge."""


class ConnectionPool:
    """Fixed-size pool that records how long each checkout held a connection."""

    def __init__(self, conn: sqlite3.Connection, clock: FakeClock, size: int) -> None:
        self._conn = conn
        self._clock = clock
        self.size = size
        self.in_use = 0
        self.hold_times: list[float] = []

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        """Check out a connection; raise PoolExhausted when all are busy."""
        if self.in_use >= self.size:
            raise PoolExhausted(f"all {self.size} connections are in use")
        self.in_use += 1
        started = self._clock.now
        try:
            yield self._conn
        finally:
            self.in_use -= 1
            self.hold_times.append(self._clock.now - started)


class FakePaymentProvider:
    """Simulated HTTP payment API with configurable latency.

    It records what the caller was holding at the moment of the call, and can
    run ``during_charge`` to model other customers checking out meanwhile.
    """

    def __init__(self, clock: FakeClock, pool: ConnectionPool, latency_seconds: float) -> None:
        self._clock = clock
        self._pool = pool
        self.latency_seconds = latency_seconds
        self.decline = False
        self.during_charge: Optional[Callable[[], None]] = None
        self.connections_held_during_calls: list[int] = []
        self.charged_keys: list[str] = []

    def charge(self, idempotency_key: str, amount_cents: int) -> None:
        """Take the money (or decline) after ``latency_seconds``."""
        self.connections_held_during_calls.append(self._pool.in_use)
        if self.during_charge is not None:
            hook, self.during_charge = self.during_charge, None  # run once
            hook()
        self._clock.advance(self.latency_seconds)
        if self.decline:
            raise PaymentDeclined(idempotency_key)
        if idempotency_key not in self.charged_keys:
            self.charged_keys.append(idempotency_key)


def create_database() -> sqlite3.Connection:
    """In-memory DB in autocommit mode so BEGIN/COMMIT below are explicit."""
    conn = sqlite3.connect(":memory:", isolation_level=None)
    conn.execute(SCHEMA)
    return conn


def order_status(pool: ConnectionPool, order_id: str) -> Optional[str]:
    """Read one order's status, or None if it does not exist."""
    with pool.connection() as conn:
        row = conn.execute("SELECT status FROM orders WHERE id = ?", (order_id,)).fetchone()
    return None if row is None else row[0]


# --- the original, buggy handler ---------------------------------------------
def naive_place_order(pool: ConnectionPool, provider: FakePaymentProvider, clock: FakeClock,
                      order_id: str, amount_cents: int) -> str:
    """One transaction wrapped around the slow HTTP call (the bug)."""
    with pool.connection() as conn:
        conn.execute("BEGIN IMMEDIATE")  # takes the write lock
        try:
            conn.execute("INSERT INTO orders VALUES (?, ?, ?, ?)",
                         (order_id, amount_cents, STATUS_PENDING, clock.now))
            provider.charge(order_id, amount_cents)  # connection + lock held here
            conn.execute("UPDATE orders SET status = ? WHERE id = ?", (STATUS_PAID, order_id))
            clock.advance(DB_WORK_SECONDS)
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    return STATUS_PAID


# --- the fix ------------------------------------------------------------------
def record_pending_order(pool: ConnectionPool, clock: FakeClock, order_id: str, amount_cents: int) -> None:
    """Short transaction #1: persist intent before any money moves."""
    with pool.connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("INSERT INTO orders VALUES (?, ?, ?, ?)",
                     (order_id, amount_cents, STATUS_PENDING, clock.now))
        clock.advance(DB_WORK_SECONDS)
        conn.execute("COMMIT")


def record_payment_outcome(pool: ConnectionPool, clock: FakeClock, order_id: str, status: str) -> None:
    """Short transaction #2: only a still-PENDING order may move to a final state."""
    with pool.connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        # WHY the status guard: the reconciler may have finished this order already.
        conn.execute("UPDATE orders SET status = ? WHERE id = ? AND status = ?",
                     (status, order_id, STATUS_PENDING))
        clock.advance(DB_WORK_SECONDS)
        conn.execute("COMMIT")


def place_order(pool: ConnectionPool, provider: FakePaymentProvider, clock: FakeClock,
                order_id: str, amount_cents: int) -> str:
    """Commit PENDING, call the provider with no connection held, then record the result."""
    record_pending_order(pool, clock, order_id, amount_cents)
    try:
        # The order id is the idempotency key, so a retry by the reconciler cannot double-charge.
        provider.charge(order_id, amount_cents)
    except PaymentDeclined:
        record_payment_outcome(pool, clock, order_id, STATUS_FAILED)
        return STATUS_FAILED
    record_payment_outcome(pool, clock, order_id, STATUS_PAID)
    return STATUS_PAID


def find_stuck_pending_orders(pool: ConnectionPool, clock: FakeClock, older_than_seconds: float) -> list[str]:
    """Orders left PENDING (e.g. the process died mid-call) for a reconciler to re-check."""
    cutoff = clock.now - older_than_seconds
    with pool.connection() as conn:
        rows = conn.execute("SELECT id FROM orders WHERE status = ? AND created_at <= ? ORDER BY id",
                            (STATUS_PENDING, cutoff)).fetchall()
    return [row[0] for row in rows]


def connections_needed(requests_per_second: float, hold_seconds: float) -> float:
    """Little's law: average connections in use = arrival rate x time each one is held."""
    return requests_per_second * hold_seconds
