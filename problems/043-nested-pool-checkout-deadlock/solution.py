"""Connection pool deadlock: a request that checks out a second connection.

The order handler checks out a database connection, starts a transaction, and
then calls a shared helper (``load_customer_tier``) that checks out *its own*
connection from the same pool. At low load this works. When as many requests
arrive at once as the pool has connections, each one holds one connection and
waits forever for a second one that only another waiting request could free.

The fix uses one connection per request: the helper takes the request's
connection as a parameter instead of reaching into the pool. A strict pool
mode also raises ``NestedCheckoutError`` the moment one request asks for a
second connection, so this bug fails in a unit test instead of in production.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# Steps a simulated request performs, one per scheduler tick.
CHECKOUT = "checkout"   # take a connection from the pool (waits if none free)
QUERY = "query"         # run a statement on a connection already held
RELEASE = "release"     # give the most recently taken connection back



class NestedCheckoutError(RuntimeError):
    """A request asked for a second connection while already holding one."""


class ConnectionPool:
    """A fixed number of connections, tracked as how many each request holds."""

    def __init__(self, size: int, strict: bool = False) -> None:
        if size < 1:
            raise ValueError("pool size must be at least 1")
        self.size = size
        # In strict mode a second checkout by the same request is a bug.
        self.strict = strict
        self.held_by: dict[str, int] = {}

    def in_use(self) -> int:
        """How many connections are checked out right now."""
        total = 0
        for count in self.held_by.values():
            total += count
        return total

    def try_checkout(self, request_id: str) -> bool:
        """Give ``request_id`` a connection if one is free; False means wait."""
        already_held = self.held_by.get(request_id, 0)
        if self.strict and already_held > 0:
            raise NestedCheckoutError(
                f"{request_id} already holds a connection; pass it to the helper "
                "instead of checking out another one")
        if self.in_use() >= self.size:
            return False
        self.held_by[request_id] = already_held + 1
        return True

    def release(self, request_id: str) -> None:
        """Return one connection held by ``request_id``."""
        held = self.held_by.get(request_id, 0)
        if held == 0:
            raise ValueError(f"{request_id} holds no connection")
        if held == 1:
            del self.held_by[request_id]
        else:
            self.held_by[request_id] = held - 1


def naive_place_order_steps() -> list[str]:
    """Steps of the original handler, which checks out a second connection.

        with pool.connection() as order_conn:           # CHECKOUT
            order_conn.execute("BEGIN; INSERT order")    # QUERY
            tier = load_customer_tier(customer_id)       # CHECKOUT, QUERY, RELEASE
            order_conn.execute("UPDATE total; COMMIT")   # QUERY
                                                         # RELEASE
    """
    return [CHECKOUT, QUERY, CHECKOUT, QUERY, RELEASE, QUERY, RELEASE]


def place_order_steps() -> list[str]:
    """Steps of the fixed handler: the helper reuses the request's connection.

        with pool.connection() as conn:                  # CHECKOUT
            conn.execute("BEGIN; INSERT order")          # QUERY
            tier = load_customer_tier(conn, customer_id) # QUERY
            conn.execute("UPDATE total; COMMIT")         # QUERY
                                                         # RELEASE
    """
    return [CHECKOUT, QUERY, QUERY, QUERY, RELEASE]


@dataclass
class SimulatedRequest:
    """One in-flight request and how far through its steps it has got."""

    request_id: str
    steps: list[str]
    next_step: int = 0

    def is_done(self) -> bool:
        """True once every step has run."""
        return self.next_step >= len(self.steps)


@dataclass
class BurstResult:
    """What happened to a burst of simultaneous requests."""

    completed: int
    stuck: int
    ticks: int
    stuck_holding: dict[str, int] = field(default_factory=dict)


def try_step(pool: ConnectionPool, request: SimulatedRequest) -> bool:
    """Run the request's next step if possible; return True if it moved on."""
    step = request.steps[request.next_step]
    if step == CHECKOUT:
        if not pool.try_checkout(request.request_id):
            # Pool is empty: this request waits, exactly like a blocking
            # pool.get() with no timeout.
            return False
    elif step == RELEASE:
        pool.release(request.request_id)
    request.next_step += 1
    return True


def run_burst(steps: list[str], concurrent_requests: int, pool: ConnectionPool,
              max_ticks: int = 1_000) -> BurstResult:
    """Start ``concurrent_requests`` at once and run them tick by tick.

    Each tick, every unfinished request tries its next step in turn. If a
    whole tick passes with no request able to move, nothing will ever free
    a connection again: that is the deadlock, and the simulation stops.
    """
    requests = []
    for index in range(concurrent_requests):
        requests.append(SimulatedRequest(f"req-{index}", list(steps)))
    ticks = 0
    while ticks < max_ticks:
        unfinished = [request for request in requests if not request.is_done()]
        if not unfinished:
            break
        ticks += 1
        progressed = False
        for request in unfinished:
            if try_step(pool, request):
                progressed = True
        if not progressed:
            break
    completed = sum(1 for request in requests if request.is_done())
    return BurstResult(completed=completed,
                       stuck=concurrent_requests - completed,
                       ticks=ticks,
                       stuck_holding=dict(pool.held_by))
