"""Deadline propagation across service calls.

The original bug: the API gateway gives each checkout request 2 s. Inside, the
checkout service called inventory, pricing and payment one after another, each
with its *own* fixed 2 s timeout. A slow request could therefore run for up to
6 s. After 2 s the gateway had already returned 504 to the customer, but the
checkout service carried on and even charged the card, for a response nobody
would ever read. The customer retried, and some were charged twice.

Fix: create one ``Deadline`` at the edge and pass it down. Every outgoing call
gets ``timeout = min(per_call_cap, remaining)``, the remaining budget is sent
downstream in a header, and work (especially side effects) is not started once
too little budget is left.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

DEADLINE_HEADER = "X-Request-Timeout-Ms"


class FakeClock:
    """Monotonic test clock in seconds."""

    def __init__(self, start: float = 0.0):
        self.t = start

    def now(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


class DeadlineExceeded(Exception):
    """Raised instead of starting work that cannot finish in time."""


@dataclass
class Deadline:
    clock: FakeClock
    expires_at: float

    @classmethod
    def after(cls, clock, seconds: float) -> "Deadline":
        return cls(clock, clock.now() + seconds)

    def remaining(self) -> float:
        return max(0.0, self.expires_at - self.clock.now())

    def expired(self) -> bool:
        return self.remaining() <= 0.0

    def to_header(self) -> dict[str, str]:
        """Propagate *remaining* milliseconds, not an absolute timestamp:
        relative budgets are immune to clock skew between hosts."""
        return {DEADLINE_HEADER: str(int(self.remaining() * 1000))}

    @classmethod
    def from_headers(cls, clock, headers: dict[str, str], default_s: float, max_s: float) -> "Deadline":
        """Build the local deadline from an incoming request.

        Missing or malformed headers fall back to ``default_s``; values above
        ``max_s`` are clamped so a caller cannot ask us to work forever.
        A zero/negative budget yields an already-expired deadline.
        """
        raw = headers.get(DEADLINE_HEADER)
        try:
            budget = int(raw) / 1000 if raw is not None else default_s
        except ValueError:
            budget = default_s
        return cls.after(clock, min(max(budget, 0.0), max_s))


def call_with_deadline(deadline: Deadline, call: Callable[[float], object], *,
                       per_call_cap: float, min_useful: float = 0.05):
    """Run ``call(timeout)`` with the smaller of its cap and the remaining budget.

    Refuses to start when less than ``min_useful`` seconds remain: a call that
    will almost certainly time out only adds load to a struggling dependency.
    """
    remaining = deadline.remaining()
    if remaining < min_useful:
        raise DeadlineExceeded(f"only {remaining * 1000:.0f} ms left")
    return call(min(per_call_cap, remaining))


@dataclass
class Dependency:
    """Simulated downstream service whose latency is known to the test."""

    clock: FakeClock
    latency: float
    calls: list = field(default_factory=list)

    def __call__(self, timeout: float):
        self.calls.append(timeout)
        if self.latency > timeout:
            self.clock.advance(timeout)
            raise TimeoutError(f"timed out after {timeout:.2f}s")
        self.clock.advance(self.latency)
        return "ok"


def checkout_fixed_timeouts(clock, inventory, pricing, payment, per_call_timeout=2.0):
    """The buggy handler: every hop gets a fresh, full timeout."""
    inventory(per_call_timeout)
    pricing(per_call_timeout)
    payment(per_call_timeout)
    return "charged"


def checkout_with_deadline(deadline: Deadline, inventory, pricing, payment,
                           per_call_cap=2.0, payment_reserve=0.3):
    """The fix: share one budget and never start a charge we cannot finish."""
    call_with_deadline(deadline, inventory, per_call_cap=per_call_cap)
    call_with_deadline(deadline, pricing, per_call_cap=per_call_cap)
    # Side effect: require enough budget for the charge to complete, otherwise
    # fail *before* moving money rather than racing the client's timeout.
    call_with_deadline(deadline, payment, per_call_cap=per_call_cap, min_useful=payment_reserve)
    return "charged"
