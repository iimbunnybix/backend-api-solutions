"""HTTP client retries that honour the server's Retry-After header.

During a planned 2-minute database maintenance, the inventory API answered
`503 Service Unavailable` with `Retry-After: 120`. The mobile backend's client
ignored the header and retried on its own exponential schedule, so every
caller burned through all its retries in a few seconds, then failed the user's
request even though waiting would have worked. This client reads Retry-After
(both the seconds form and the HTTP-date form), waits that long when it fits
in the caller's deadline, and gives up immediately when it does not.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Protocol

RETRYABLE_STATUSES = (429, 503)
MAX_ATTEMPTS = 5
BASE_BACKOFF_SECONDS = 0.1
# A buggy or hostile server must not be able to park our workers for an hour.
MAX_RETRY_AFTER_SECONDS = 300.0


@dataclass
class Response:
    status: int
    headers: dict[str, str] = field(default_factory=dict)
    body: str = ""


class Clock(Protocol):
    def now(self) -> float: ...
    def sleep(self, seconds: float) -> None: ...


class Transport(Protocol):
    def send(self, path: str) -> Response: ...


def exponential_backoff(attempt: int) -> float:
    """Delay before retry number `attempt` (1-based): 0.1s, 0.2s, 0.4s, ..."""
    return BASE_BACKOFF_SECONDS * (2 ** (attempt - 1))


def naive_get(transport: Transport, clock: Clock, path: str) -> Response:
    """Original client: retries on its own schedule and ignores Retry-After."""
    response = transport.send(path)
    for attempt in range(1, MAX_ATTEMPTS):
        if response.status not in RETRYABLE_STATUSES:
            return response
        clock.sleep(exponential_backoff(attempt))
        response = transport.send(path)
    return response


def parse_retry_after(value: str | None, now_epoch: float) -> float | None:
    """Return the wait in seconds that a Retry-After header asks for, or None.

    RFC 9110 allows either a non-negative integer of seconds or an HTTP-date.
    Anything unparseable returns None so the caller falls back to backoff.
    """
    if value is None:
        return None
    text = value.strip()
    if text.isdigit():
        return min(float(text), MAX_RETRY_AFTER_SECONDS)
    try:
        retry_at = parsedate_to_datetime(text)
    except (TypeError, ValueError):
        return None
    if retry_at.tzinfo is None:
        # HTTP-dates are always GMT; parsedate_to_datetime returns naive for "-0000".
        retry_at = retry_at.replace(tzinfo=timezone.utc)
    wait = retry_at.timestamp() - now_epoch
    # A date in the past means "you may retry now".
    return min(max(wait, 0.0), MAX_RETRY_AFTER_SECONDS)


def next_delay(response: Response, attempt: int, now_epoch: float) -> float:
    """How long to wait before the next attempt: the server's hint wins over our guess."""
    hinted = parse_retry_after(response.headers.get("Retry-After"), now_epoch)
    if hinted is not None:
        return hinted
    return exponential_backoff(attempt)


def polite_get(transport: Transport, clock: Clock, path: str, deadline_seconds: float) -> Response:
    """GET with retries that follow Retry-After and never overrun the caller's deadline."""
    deadline = clock.now() + deadline_seconds
    response = transport.send(path)
    for attempt in range(1, MAX_ATTEMPTS):
        if response.status not in RETRYABLE_STATUSES:
            return response
        delay = next_delay(response, attempt, clock.now())
        if clock.now() + delay > deadline:
            # Waiting would blow the deadline, and retrying sooner is pointless
            # because the server told us it is not ready. Fail fast instead.
            return response
        clock.sleep(delay)
        response = transport.send(path)
    return response
