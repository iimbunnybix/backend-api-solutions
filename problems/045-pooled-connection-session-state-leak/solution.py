"""Pooled database connections leaked session state from one request to the next.

A multi-tenant API enforces tenant isolation with row-level security that reads
a session variable (`SET app.tenant_id = ...`). Connections come from a pool and
are reused, but the pool returned them exactly as the last request left them:
the tenant id, a report's `statement_timeout = 0` and even an open, failed
transaction carried over to whichever request borrowed the connection next.

The fix resets every connection when it is returned to the pool (roll back any
open transaction, then the equivalent of `DISCARD ALL`), and gives handlers a
context manager that always returns the connection, even when they raise.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator

TENANT_SETTING = "app.tenant_id"
TIMEOUT_SETTING = "statement_timeout_ms"
NO_TENANT = ""
DEFAULT_STATEMENT_TIMEOUT_MS = 30_000
UNLIMITED_TIMEOUT_MS = 0


def default_session_settings() -> dict[str, object]:
    """Settings a brand-new connection starts with (what RESET ALL restores)."""
    return {TENANT_SETTING: NO_TENANT, TIMEOUT_SETTING: DEFAULT_STATEMENT_TIMEOUT_MS}


@dataclass(frozen=True)
class Product:
    name: str
    tenant_id: str
    is_public: bool


class FakeConnection:
    """A database session: per-connection settings plus transaction state."""

    def __init__(self, connection_id: int, products: list[Product]) -> None:
        self.connection_id = connection_id
        self.products = products
        self.settings = default_session_settings()
        self.in_transaction = False

    def set_session(self, name: str, value: object) -> None:
        """Like `SET name = value`: lasts until the session is reset."""
        self.settings[name] = value

    def begin(self) -> None:
        """Start a transaction."""
        self.in_transaction = True

    def rollback(self) -> None:
        """Abort the open transaction, releasing its locks."""
        self.in_transaction = False

    def reset_session(self) -> None:
        """Like `DISCARD ALL`: forget every session-level setting."""
        self.settings = default_session_settings()

    def visible_products(self) -> list[str]:
        """Row-level security: public rows plus rows of the session's tenant."""
        current_tenant = self.settings[TENANT_SETTING]
        visible = []
        for product in self.products:
            if product.is_public or product.tenant_id == current_tenant:
                visible.append(product.name)
        return visible


class ConnectionPool:
    """Reuses idle connections, most recently returned first (as most pools do)."""

    def __init__(self, products: list[Product], reset_on_release: bool) -> None:
        self.products = products
        self.reset_on_release = reset_on_release
        self.idle: list[FakeConnection] = []
        self.created_count = 0

    def acquire(self) -> FakeConnection:
        """Hand out an idle connection, or open a new one if none is idle."""
        if self.idle:
            return self.idle.pop()
        self.created_count += 1
        return FakeConnection(self.created_count, self.products)

    def release(self, connection: FakeConnection) -> None:
        """Return a connection; the fixed pool cleans it first."""
        if self.reset_on_release:
            clean_connection(connection)
        self.idle.append(connection)


def clean_connection(connection: FakeConnection) -> None:
    """Make a used connection indistinguishable from a fresh one."""
    # Roll back first: settings changed inside a failed transaction must not
    # survive, and its locks must be released before anyone else uses it.
    if connection.in_transaction:
        connection.rollback()
    connection.reset_session()


def naive_pool(products: list[Product]) -> ConnectionPool:
    """Original pool: connections go back exactly as the last request left them."""
    return ConnectionPool(products, reset_on_release=False)


def resetting_pool(products: list[Product]) -> ConnectionPool:
    """Fixed pool: every returned connection is rolled back and reset."""
    return ConnectionPool(products, reset_on_release=True)


@contextmanager
def borrowed_connection(pool: ConnectionPool) -> Iterator[FakeConnection]:
    """Always return the connection, even when the handler raises."""
    connection = pool.acquire()
    try:
        yield connection
    finally:
        pool.release(connection)


def handle_tenant_dashboard(pool: ConnectionPool, tenant_id: str) -> list[str]:
    """Signed-in request: scope the session to the caller's tenant."""
    with borrowed_connection(pool) as connection:
        connection.set_session(TENANT_SETTING, tenant_id)
        return connection.visible_products()


def handle_public_catalogue(pool: ConnectionPool) -> list[str]:
    """Anonymous request: relies on 'no tenant set' meaning 'public rows only'."""
    with borrowed_connection(pool) as connection:
        return connection.visible_products()


def handle_monthly_report(pool: ConnectionPool) -> int:
    """Admin report that lifts the statement timeout for one long query."""
    with borrowed_connection(pool) as connection:
        connection.set_session(TIMEOUT_SETTING, UNLIMITED_TIMEOUT_MS)
        return len(connection.visible_products())


class PaymentDeclined(Exception):
    """The payment provider refused the card mid-checkout."""


def handle_failing_checkout(pool: ConnectionPool) -> None:
    """Checkout that opens a transaction and then fails before committing."""
    with borrowed_connection(pool) as connection:
        connection.begin()
        raise PaymentDeclined("card declined")
