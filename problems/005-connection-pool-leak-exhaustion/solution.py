"""Bounded database connection pool that cannot leak connections.

The original incident: a handler did ``conn = pool.get()`` and only called
``pool.put(conn)`` on the happy path. Every exception (a validation error, a
timeout from a downstream call) leaked one connection. After enough errors the
pool was empty and every request blocked forever waiting for a connection.

This module provides:

* ``ConnectionPool.connection()`` - a context manager that always returns the
  connection, and discards (instead of reusing) a connection whose transaction
  failed, so a broken connection is never handed to the next request.
* An acquire timeout that raises ``PoolTimeout`` (map to HTTP 503 with
  ``Retry-After``) instead of blocking a worker thread forever.
* Leak detection: ``leak_report()`` lists connections held longer than a
  threshold together with the stack that checked them out.
"""
from __future__ import annotations

import itertools
import threading
import time
import traceback
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Callable, Iterator


class PoolTimeout(Exception):
    """No connection became available within the acquire timeout."""


class FakeConnection:
    """Stand-in for a DB-API connection; tracks its own state for tests."""

    _ids = itertools.count(1)

    def __init__(self) -> None:
        self.id = next(self._ids)
        self.closed = False
        self.in_transaction = False
        self.rolled_back = 0

    def begin(self) -> None:
        self.in_transaction = True

    def commit(self) -> None:
        self.in_transaction = False

    def rollback(self) -> None:
        self.in_transaction = False
        self.rolled_back += 1

    def close(self) -> None:
        self.closed = True


@dataclass
class _Checkout:
    conn: FakeConnection
    since: float
    stack: str = field(repr=False)


class ConnectionPool:
    """Thread-safe pool with a hard size cap, acquire timeout and leak tracking.

    ``factory`` creates a new connection; ``clock`` is injectable for tests
    (only used for leak ages, not for the blocking wait).
    """

    def __init__(
        self,
        factory: Callable[[], FakeConnection] = FakeConnection,
        max_size: int = 10,
        acquire_timeout_s: float = 2.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if max_size < 1:
            raise ValueError("max_size must be >= 1")
        self._factory = factory
        self._max = max_size
        self._timeout = acquire_timeout_s
        self._clock = clock
        self._idle: list[FakeConnection] = []
        self._out: dict[int, _Checkout] = {}
        self._created = 0
        self._cv = threading.Condition()
        self.stats = {"acquired": 0, "timeouts": 0, "discarded": 0}

    # -- low level -------------------------------------------------------
    def acquire(self, timeout_s: float | None = None) -> FakeConnection:
        """Take a connection, creating one if under the cap, else wait.

        Raises ``PoolTimeout`` if none is free within ``timeout_s``.
        """
        timeout = self._timeout if timeout_s is None else timeout_s
        deadline = time.monotonic() + timeout
        with self._cv:
            while True:
                conn = None
                if self._idle:
                    conn = self._idle.pop()
                elif self._created < self._max:
                    self._created += 1
                    conn = self._factory()
                if conn is not None:
                    self._out[conn.id] = _Checkout(
                        conn, self._clock(), "".join(traceback.format_stack(limit=8)[:-1])
                    )
                    self.stats["acquired"] += 1
                    return conn
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self.stats["timeouts"] += 1
                    raise PoolTimeout(
                        f"no connection free after {timeout:.2f}s "
                        f"({len(self._out)}/{self._max} checked out)"
                    )
                self._cv.wait(remaining)

    def release(self, conn: FakeConnection, *, discard: bool = False) -> None:
        """Return ``conn``; ``discard=True`` closes it and frees its slot."""
        with self._cv:
            if self._out.pop(conn.id, None) is None:
                raise ValueError(f"connection {conn.id} is not checked out from this pool")
            if discard or conn.closed:
                conn.close()
                self._created -= 1
                self.stats["discarded"] += 1
            else:
                self._idle.append(conn)
            self._cv.notify()

    # -- the API handlers should use --------------------------------------
    @contextmanager
    def connection(self) -> Iterator[FakeConnection]:
        """Yield a connection inside a transaction; always give it back.

        Commit on success. On any exception, roll back; if the rollback itself
        fails the connection is in an unknown state and is discarded.
        """
        conn = self.acquire()
        discard = False
        try:
            conn.begin()
            yield conn
            conn.commit()
        except BaseException:
            try:
                conn.rollback()
            except Exception:
                discard = True
            raise
        finally:
            self.release(conn, discard=discard)

    # -- observability ----------------------------------------------------
    def in_use(self) -> int:
        with self._cv:
            return len(self._out)

    def leak_report(self, older_than_s: float) -> list[tuple[int, float, str]]:
        """(conn id, held seconds, checkout stack) for long-held connections."""
        now = self._clock()
        with self._cv:
            return [
                (c.conn.id, now - c.since, c.stack)
                for c in self._out.values()
                if now - c.since >= older_than_s
            ]


# --- handlers -----------------------------------------------------------
def leaky_handler(pool: ConnectionPool, order_id: int) -> str:
    """The original bug: release is skipped whenever validation raises."""
    conn = pool.acquire()
    conn.begin()
    if order_id < 0:
        raise ValueError("bad order id")  # connection never returned
    conn.commit()
    pool.release(conn)
    return f"order {order_id}"


def fixed_handler(pool: ConnectionPool, order_id: int) -> str:
    """Same logic, but the context manager guarantees release."""
    with pool.connection():
        if order_id < 0:
            raise ValueError("bad order id")
        return f"order {order_id}"
