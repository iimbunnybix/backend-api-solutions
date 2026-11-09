"""Bulkheads: stop one slow dependency from taking every request worker.

A checkout API serves all routes from one shared pool of worker slots. When the
recommendations service started answering in 30 s instead of 50 ms, every
worker ended up waiting on it and checkout requests were turned away with 503,
even though the payment service was healthy.

The fix gives each downstream dependency its own small compartment (a
bulkhead): a counter of how many workers it may hold at once. When a
compartment is full, the dependency call is skipped instead of taking a
worker from the shared pool, so the other routes keep their capacity. For an
optional dependency (recommendations) the request is still served, just
without that part; for a required one it is rejected fast.
"""
from __future__ import annotations

from dataclasses import dataclass, field

ACCEPTED = "accepted"
REJECTED_POOL_FULL = "rejected_pool_full"
ACCEPTED_WITHOUT_DEPENDENCY = "accepted_without_dependency"
REJECTED_BULKHEAD_FULL = "rejected_bulkhead_full"


@dataclass
class Request:
    """One incoming request: which dependency it calls and how long it holds a worker."""

    dependency: str
    duration_seconds: float


@dataclass
class WorkerPool:
    """A fixed number of worker slots tracked as the times each busy slot frees up."""

    capacity: int
    busy_until: list[float] = field(default_factory=list)

    def release_finished(self, now: float) -> None:
        """Free every slot whose request has finished by ``now``."""
        still_busy = []
        for finish_time in self.busy_until:
            if finish_time > now:
                still_busy.append(finish_time)
        self.busy_until = still_busy

    def has_free_slot(self) -> bool:
        """True when at least one worker is idle."""
        return len(self.busy_until) < self.capacity

    def occupy(self, now: float, duration_seconds: float) -> None:
        """Mark one worker busy until ``now + duration_seconds``."""
        self.busy_until.append(now + duration_seconds)


class NaiveSharedPoolServer:
    """Original server: any request may take any free worker."""

    def __init__(self, pool_size: int) -> None:
        self.pool = WorkerPool(pool_size)

    def handle(self, request: Request, now: float) -> str:
        """Accept the request if any worker is free, otherwise reject with 503."""
        self.pool.release_finished(now)
        if not self.pool.has_free_slot():
            return REJECTED_POOL_FULL
        self.pool.occupy(now, request.duration_seconds)
        return ACCEPTED


class BulkheadServer:
    """Fixed server: each dependency may hold at most its own share of workers.

    ``fallback_seconds`` lists optional dependencies: when their compartment is
    full the request is still served without them, holding a worker only for
    that (short) time. Dependencies not listed there are rejected fast.
    """

    def __init__(self, pool_size: int, bulkhead_limits: dict[str, int],
                 fallback_seconds: dict[str, float] | None = None) -> None:
        self.pool = WorkerPool(pool_size)
        self.bulkhead_limits = bulkhead_limits
        self.fallback_seconds = dict(fallback_seconds or {})
        # Finish times of in-flight requests, per dependency.
        self.in_flight: dict[str, list[float]] = {name: [] for name in bulkhead_limits}

    def _release_finished(self, now: float) -> None:
        """Free finished requests both from the pool and from their compartments."""
        self.pool.release_finished(now)
        for name, finish_times in self.in_flight.items():
            self.in_flight[name] = [t for t in finish_times if t > now]

    def _compartment_is_full(self, dependency: str) -> bool:
        """True when this dependency already holds as many workers as it is allowed."""
        limit = self.bulkhead_limits.get(dependency)
        if limit is None:
            # Dependencies without an explicit limit share whatever is left.
            return False
        return len(self.in_flight[dependency]) >= limit

    def _serve_without_dependency(self, request: Request, now: float) -> str:
        """Skip a full compartment: degrade if a fallback exists, else reject fast."""
        fallback = self.fallback_seconds.get(request.dependency)
        if fallback is None:
            return REJECTED_BULKHEAD_FULL
        if not self.pool.has_free_slot():
            return REJECTED_POOL_FULL
        # The fallback does only the request's own work, so it holds a shared
        # worker briefly and does not count against the dependency's compartment.
        self.pool.occupy(now, fallback)
        return ACCEPTED_WITHOUT_DEPENDENCY

    def handle(self, request: Request, now: float) -> str:
        """Skip the dependency when its compartment is full; else use the pool."""
        self._release_finished(now)
        # Check the compartment first: a slow dependency must be refused
        # *before* it gets the chance to take a shared worker.
        if self._compartment_is_full(request.dependency):
            return self._serve_without_dependency(request, now)
        if not self.pool.has_free_slot():
            return REJECTED_POOL_FULL
        self.pool.occupy(now, request.duration_seconds)
        if request.dependency in self.in_flight:
            self.in_flight[request.dependency].append(now + request.duration_seconds)
        return ACCEPTED


def run_traffic(server: NaiveSharedPoolServer | BulkheadServer,
                arrivals_per_second: dict[str, tuple[int, float]],
                seconds: int) -> dict[str, dict[str, int]]:
    """Send the same requests every second and count outcomes per dependency.

    ``arrivals_per_second`` maps dependency -> (requests per second, duration).
    """
    outcomes: dict[str, dict[str, int]] = {}
    for second in range(seconds):
        for dependency, (count, duration) in arrivals_per_second.items():
            per_dependency = outcomes.setdefault(dependency, {})
            for _ in range(count):
                result = server.handle(Request(dependency, duration), float(second))
                per_dependency[result] = per_dependency.get(result, 0) + 1
    return outcomes
