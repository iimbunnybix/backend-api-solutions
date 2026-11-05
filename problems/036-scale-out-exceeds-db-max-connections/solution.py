"""Size each instance's connection pool from a shared database budget.

Every API instance opened a fixed pool of 20 database connections. That was
fine at 10 instances (200 connections), but when the autoscaler went to 40
instances the pools added up to 800 against a database limit of 500. New
instances failed at startup with "too many connections", and migrations and
admin sessions could not get a connection either.

The fix treats ``max_connections`` as a budget shared by the whole fleet:
subtract slots reserved for admin/migration clients, divide the rest by the
largest number of instances that can ever run at once (autoscaler maximum
plus deploy surge), and cap each pool at that share. Bad inputs fail at
startup with an error that says what to change.
"""
from __future__ import annotations

from dataclasses import dataclass

DB_MAX_CONNECTIONS = 500        # the database server's max_connections
RESERVED_CONNECTIONS = 20       # kept free for migrations, admin shells, monitoring
DESIRED_POOL_SIZE = 20          # what one instance would like when there is room
MIN_USEFUL_POOL_SIZE = 2        # below this one slow query blocks the instance


class TooManyConnections(Exception):
    """The database refused a new connection (PostgreSQL: 'too many clients already')."""


class PoolBudgetError(ValueError):
    """The connection budget cannot give every instance a usable pool."""


class FakeDatabase:
    """Counts open connections and refuses new ones above ``max_connections``."""

    def __init__(self, max_connections: int = DB_MAX_CONNECTIONS) -> None:
        self.max_connections = max_connections
        self.open_connections = 0

    def connect(self) -> None:
        """Open one connection or raise TooManyConnections."""
        if self.open_connections >= self.max_connections:
            raise TooManyConnections(
                f"too many connections ({self.max_connections} in use)")
        self.open_connections += 1

    def close(self, count: int) -> None:
        """Close ``count`` connections."""
        self.open_connections -= count


@dataclass
class PoolBudget:
    """Inputs for sizing pools. ``max_instances`` is the autoscaler maximum."""

    db_max_connections: int = DB_MAX_CONNECTIONS
    reserved_connections: int = RESERVED_CONNECTIONS
    max_instances: int = 40
    surge_instances: int = 0      # extra instances alive during a rolling deploy
    desired_pool_size: int = DESIRED_POOL_SIZE


def naive_pool_size(budget: PoolBudget) -> int:
    """Original config: every instance gets the same fixed pool, whatever the fleet size."""
    return budget.desired_pool_size


def _validate(budget: PoolBudget) -> None:
    """Reject inputs that make the arithmetic meaningless."""
    if budget.max_instances < 1:
        raise PoolBudgetError(f"max_instances must be at least 1, got {budget.max_instances}")
    if budget.surge_instances < 0:
        raise PoolBudgetError(f"surge_instances cannot be negative, got {budget.surge_instances}")
    if budget.reserved_connections < 0:
        raise PoolBudgetError(
            f"reserved_connections cannot be negative, got {budget.reserved_connections}")
    if budget.reserved_connections >= budget.db_max_connections:
        raise PoolBudgetError(
            f"reserved_connections ({budget.reserved_connections}) must be below "
            f"db_max_connections ({budget.db_max_connections})")
    if budget.desired_pool_size < MIN_USEFUL_POOL_SIZE:
        raise PoolBudgetError(
            f"desired_pool_size must be at least {MIN_USEFUL_POOL_SIZE}, "
            f"got {budget.desired_pool_size}")


def pool_size_from_budget(budget: PoolBudget) -> int:
    """Per-instance pool size that keeps the whole fleet within max_connections.

    The divisor is the most instances that can run at once, not the current
    count: pools are sized at startup, and the fleet can grow afterwards.
    """
    _validate(budget)
    app_connections = budget.db_max_connections - budget.reserved_connections
    peak_instances = budget.max_instances + budget.surge_instances
    # Floor division: rounding up would overshoot the limit by up to one
    # connection per instance.
    fair_share = app_connections // peak_instances
    if fair_share < MIN_USEFUL_POOL_SIZE:
        raise PoolBudgetError(
            f"{app_connections} app connections / {peak_instances} instances = "
            f"{fair_share} per instance, below the minimum of {MIN_USEFUL_POOL_SIZE}. "
            "Raise max_connections, lower the autoscaler maximum, or put a "
            "connection pooler (e.g. PgBouncer) in front of the database.")
    # Never more than the instance wants: spare budget stays unused, not hoarded.
    return min(budget.desired_pool_size, fair_share)


@dataclass
class ScaleOutResult:
    healthy_instances: int
    failed_instances: int
    connections_in_use: int


def start_instance(db: FakeDatabase, pool_size: int) -> bool:
    """Open a fixed pool. On failure release what was opened and report unhealthy."""
    opened = 0
    try:
        for _ in range(pool_size):
            db.connect()
            opened += 1
    except TooManyConnections:
        # The health check fails and the orchestrator kills the instance, so
        # its partial pool is closed again.
        db.close(opened)
        return False
    return True


def scale_out(db: FakeDatabase, instance_count: int, pool_size: int) -> ScaleOutResult:
    """Start ``instance_count`` instances one after another, each with ``pool_size``."""
    healthy = 0
    failed = 0
    for _ in range(instance_count):
        if start_instance(db, pool_size):
            healthy += 1
        else:
            failed += 1
    return ScaleOutResult(healthy, failed, db.open_connections)


def free_connections(db: FakeDatabase) -> int:
    """Slots left for migrations and admin sessions."""
    return db.max_connections - db.open_connections
