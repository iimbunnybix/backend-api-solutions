"""The webhook sender treated every status except 200 as a failed delivery.

Many receivers answer ``202 Accepted`` or ``204 No Content``. The sender saw
those as failures and retried with backoff, so each event was processed by
the receiver up to six times. It also retried ``400``/``404`` answers that
can never succeed. The fix classifies responses by status class: any 2xx is
delivered, most 4xx are permanent failures that are not retried, and only
5xx, 408, 429 and network errors are retried. Every attempt carries the same
event id, so a receiver can still drop a genuine duplicate.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Callable

MAX_ATTEMPTS = 6                      # first try + 5 retries
HTTP_OK = 200
HTTP_REQUEST_TIMEOUT = 408
HTTP_TOO_MANY_REQUESTS = 429
NETWORK_ERROR = 0                     # stands for "no HTTP response at all"


class Outcome(Enum):
    DELIVERED = "delivered"
    RETRY = "retry"
    GIVE_UP = "give_up"


@dataclass
class FakeReceiver:
    """A customer endpoint that answers with a scripted list of statuses.

    It records every event id it processed, so tests can count duplicates.
    After the script runs out it keeps returning the last status.
    """

    statuses: list[int]
    processed: list[str] = field(default_factory=list)
    calls: int = 0

    def handle(self, event_id: str) -> int:
        """Answer one delivery attempt."""
        status = self.statuses[min(self.calls, len(self.statuses) - 1)]
        self.calls += 1
        # A 2xx means the receiver did the work, even if we never hear back.
        if 200 <= status < 300:
            self.processed.append(event_id)
        return status


# ---------------------------------------------------------------- original
def naive_classify(status: int) -> Outcome:
    """Original rule: only 200 counts, everything else is retried."""
    if status == HTTP_OK:
        return Outcome.DELIVERED
    return Outcome.RETRY


# ---------------------------------------------------------------- the fix
def classify(status: int) -> Outcome:
    """Decide what a response means for this delivery.

    2xx: the receiver accepted it. 408/429/5xx and network errors are
    temporary, so a later attempt may work. Every other 4xx (bad payload,
    gone endpoint, auth failure) will fail the same way again, so retrying
    only adds load. 3xx is not followed: a redirect could point a signed
    payload at a host the customer never registered.
    """
    if 200 <= status < 300:
        return Outcome.DELIVERED
    if status in (NETWORK_ERROR, HTTP_REQUEST_TIMEOUT, HTTP_TOO_MANY_REQUESTS):
        return Outcome.RETRY
    if status >= 500:
        return Outcome.RETRY
    return Outcome.GIVE_UP


@dataclass(frozen=True)
class DeliveryResult:
    outcome: Outcome
    attempts: int
    last_status: int


def deliver(event_id: str, receiver: FakeReceiver,
            classify_status: Callable[[int], Outcome] = classify) -> DeliveryResult:
    """Try to deliver one event, retrying only while the classifier says so.

    Backoff waits are left out: they change when attempts happen, not how
    many happen, which is what this problem is about.
    """
    attempts = 0
    status = NETWORK_ERROR
    while attempts < MAX_ATTEMPTS:
        attempts += 1
        # The same event id on every attempt lets the receiver deduplicate.
        status = receiver.handle(event_id)
        outcome = classify_status(status)
        if outcome is not Outcome.RETRY:
            return DeliveryResult(outcome, attempts, status)
    return DeliveryResult(Outcome.GIVE_UP, attempts, status)
