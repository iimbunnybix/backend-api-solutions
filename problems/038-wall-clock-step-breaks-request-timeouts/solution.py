"""Measure request timeouts with a monotonic clock, not the wall clock.

The API's watchdog cancelled any request running longer than 30 seconds by
computing ``time.time() - started_at``. ``time.time()`` is the wall clock: NTP,
a VM resume or an operator can step it backwards or forwards. A step back made
elapsed times negative so nothing timed out and stuck requests piled up; a
step forward made every in-flight request look ancient and they were all
cancelled at once.

The fix measures durations with ``time.monotonic()``, which only moves forward
at a steady rate and ignores wall-clock steps. Wall time is still recorded,
but only for logs.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, List

REQUEST_TIMEOUT_SECONDS = 30.0

Clock = Callable[[], float]


class FakeHostClocks:
    """A host's two clocks, controllable from tests.

    ``wall()`` behaves like ``time.time()`` and can be stepped by NTP.
    ``monotonic()`` behaves like ``time.monotonic()`` and only advances.
    """

    def __init__(self, wall_start: float = 1_700_000_000.0) -> None:
        self._wall = wall_start
        self._monotonic = 0.0

    def wall(self) -> float:
        return self._wall

    def monotonic(self) -> float:
        return self._monotonic

    def advance(self, seconds: float) -> None:
        """Real time passes: both clocks move forward together."""
        self._wall += seconds
        self._monotonic += seconds

    def step_wall(self, seconds: float) -> None:
        """NTP (or an operator) jumps the wall clock; monotonic is unaffected."""
        self._wall += seconds


@dataclass
class InFlightRequest:
    request_id: str
    started_at: float          # reading of the watchdog's clock at start


class TimeoutWatchdog:
    """Tracks in-flight requests and reports the ones over the timeout.

    The clock is injected so the same code shows the bug (wall clock) and the
    fix (monotonic clock).
    """

    def __init__(self, clock: Clock, timeout_seconds: float = REQUEST_TIMEOUT_SECONDS) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self._clock = clock
        self._timeout = timeout_seconds
        self._in_flight: Dict[str, InFlightRequest] = {}

    def start(self, request_id: str) -> None:
        self._in_flight[request_id] = InFlightRequest(request_id, self._clock())

    def finish(self, request_id: str) -> float:
        """Remove a finished request and return how long it took."""
        request = self._in_flight.pop(request_id)
        return self._clock() - request.started_at

    def elapsed(self, request_id: str) -> float:
        return self._clock() - self._in_flight[request_id].started_at

    def timed_out(self) -> List[str]:
        """IDs of requests that have run for at least the timeout."""
        expired = []
        for request_id in sorted(self._in_flight):
            if self.elapsed(request_id) >= self._timeout:
                expired.append(request_id)
        return expired


def naive_watchdog(clocks: FakeHostClocks) -> TimeoutWatchdog:
    """Original wiring: durations measured with the wall clock (time.time)."""
    return TimeoutWatchdog(clocks.wall)


def safe_watchdog(clocks: FakeHostClocks) -> TimeoutWatchdog:
    """Fixed wiring: durations measured with the monotonic clock."""
    return TimeoutWatchdog(clocks.monotonic)
