"""Circuit breaker that stops a slow, optional dependency from taking down its caller.

The product page calls a recommendations service. When that service hangs,
every page request waits for the full client timeout and holds a worker
thread while it does. A circuit breaker watches recent outcomes and, once
most of them are failures (errors *or* calls slower than a threshold),
short-circuits further calls to a fallback for a cool-down period. After the
cool-down it lets a few probe calls through ("half-open") to decide whether
to close again.
"""
from __future__ import annotations

import threading
import time
from collections import deque
from enum import Enum
from typing import Callable, Deque, Optional, TypeVar

T = TypeVar("T")


class State(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitOpenError(RuntimeError):
    """Raised (or turned into a fallback) when the breaker refuses a call."""


class FakeClock:
    """Deterministic monotonic clock for tests and simulations."""

    def __init__(self, start: float = 0.0) -> None:
        self.t = start

    def now(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


class CircuitBreaker:
    """Count-based circuit breaker.

    Args:
        window_size: number of most recent calls considered.
        min_calls: do not trip before this many calls are in the window
            (avoids opening on 1 failure out of 1 call).
        failure_rate_threshold: open when failures / calls >= this.
        slow_call_seconds: a *successful* call slower than this also counts as
            a failure, because a hung dependency hurts as much as an erroring one.
        open_seconds: how long to fail fast before probing again.
        half_open_max_calls: number of concurrent probe calls allowed while
            half-open; all of them must succeed to close the breaker.
        clock: callable returning monotonic seconds.
    """

    def __init__(self, *, window_size: int = 20, min_calls: int = 10,
                 failure_rate_threshold: float = 0.5, slow_call_seconds: float = 1.0,
                 open_seconds: float = 30.0, half_open_max_calls: int = 3,
                 clock: Callable[[], float] = time.monotonic) -> None:
        if not 0 < failure_rate_threshold <= 1:
            raise ValueError("failure_rate_threshold must be in (0, 1]")
        if min_calls > window_size:
            raise ValueError("min_calls cannot exceed window_size")
        self.window_size = window_size
        self.min_calls = min_calls
        self.failure_rate_threshold = failure_rate_threshold
        self.slow_call_seconds = slow_call_seconds
        self.open_seconds = open_seconds
        self.half_open_max_calls = half_open_max_calls
        self._clock = clock
        self._lock = threading.Lock()
        self._outcomes: Deque[bool] = deque(maxlen=window_size)  # True = failure
        self._state = State.CLOSED
        self._opened_at = 0.0
        self._probes_in_flight = 0
        self._probe_successes = 0
        self.short_circuited = 0

    # ------------------------------------------------------------------ state
    @property
    def state(self) -> State:
        with self._lock:
            self._maybe_half_open()
            return self._state

    def _maybe_half_open(self) -> None:
        if self._state is State.OPEN and self._clock() - self._opened_at >= self.open_seconds:
            self._state = State.HALF_OPEN
            self._probes_in_flight = 0
            self._probe_successes = 0

    def _trip(self) -> None:
        self._state = State.OPEN
        self._opened_at = self._clock()
        self._outcomes.clear()

    def _acquire(self) -> bool:
        """Decide whether a call may proceed. Returns True if it is a probe."""
        with self._lock:
            self._maybe_half_open()
            if self._state is State.CLOSED:
                return False
            if self._state is State.HALF_OPEN and self._probes_in_flight < self.half_open_max_calls:
                self._probes_in_flight += 1
                return True
            self.short_circuited += 1
            raise CircuitOpenError(f"circuit {self._state.value}")

    def _record(self, failed: bool, probe: bool) -> None:
        with self._lock:
            if probe:
                self._probes_in_flight -= 1
                if self._state is not State.HALF_OPEN:
                    return  # another probe already decided the outcome
                if failed:
                    self._trip()
                    return
                self._probe_successes += 1
                if self._probe_successes >= self.half_open_max_calls:
                    self._state = State.CLOSED
                    self._outcomes.clear()
                return
            if self._state is not State.CLOSED:
                return  # late result from a call started before the trip
            self._outcomes.append(failed)
            calls = len(self._outcomes)
            if calls >= self.min_calls and sum(self._outcomes) / calls >= self.failure_rate_threshold:
                self._trip()

    # ------------------------------------------------------------------- call
    def call(self, fn: Callable[[], T], fallback: Optional[Callable[[], T]] = None) -> T:
        """Run ``fn`` through the breaker.

        If the breaker refuses the call, or ``fn`` raises, ``fallback`` is used
        when given; otherwise the exception propagates.
        """
        try:
            probe = self._acquire()
        except CircuitOpenError:
            if fallback is not None:
                return fallback()
            raise
        start = self._clock()
        try:
            result = fn()
        except Exception:
            self._record(True, probe)
            if fallback is not None:
                return fallback()
            raise
        slow = self._clock() - start > self.slow_call_seconds
        self._record(slow, probe)
        return result


# ---------------------------------------------------------------- simulation
class FlakyDependency:
    """Recommendations client stub: either answers in ``latency`` or hangs until ``timeout``."""

    def __init__(self, clock: FakeClock, latency: float = 0.05, timeout: float = 3.0) -> None:
        self.clock = clock
        self.latency = latency
        self.timeout = timeout
        self.down = False
        self.calls = 0

    def __call__(self) -> list[str]:
        self.calls += 1
        if self.down:
            self.clock.advance(self.timeout)
            raise TimeoutError("recommendations timed out")
        self.clock.advance(self.latency)
        return ["sku-1", "sku-2", "sku-3"]


def worker_capacity_rps(workers: int, seconds_per_request: float) -> float:
    """Little's law: a pool of ``workers`` threads serves at most workers / service_time req/s."""
    return workers / seconds_per_request
