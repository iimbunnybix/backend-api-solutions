"""Rate limiting by request count let one batch endpoint call do 100 lookups.

A contacts API limited each API key to 100 requests per minute. The batch
endpoint accepts up to 100 contact ids per call, but it was charged like any
other request: one token. A sync script that switched to the batch endpoint
did 100 times its quota in database lookups, and nothing was ever throttled.

The fix charges every call by the work it causes (one token per contact id,
at least one token per call), and rejects batches that could never fit in the
bucket with a clear error instead of letting them wait forever.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable

REQUESTS_PER_MINUTE = 100
SECONDS_PER_MINUTE = 60.0
BUCKET_CAPACITY = REQUESTS_PER_MINUTE
MAX_BATCH_SIZE = 100
MIN_COST_PER_CALL = 1

HTTP_OK = 200
HTTP_BAD_REQUEST = 400
HTTP_TOO_MANY_REQUESTS = 429


class FakeClock:
    """A clock the tests move forward by hand."""

    def __init__(self) -> None:
        self.now = 0.0

    def advance(self, seconds: float) -> None:
        """Move time forward."""
        self.now += seconds


class TokenBucket:
    """Holds up to `capacity` tokens and refills at a steady rate."""

    def __init__(self, clock: FakeClock, capacity: float, refill_per_second: float) -> None:
        self.clock = clock
        self.capacity = capacity
        self.refill_per_second = refill_per_second
        self.tokens = capacity
        self.last_refill = clock.now

    def _refill(self) -> None:
        """Add the tokens earned since the last call, up to capacity."""
        elapsed = self.clock.now - self.last_refill
        self.tokens = min(self.capacity, self.tokens + elapsed * self.refill_per_second)
        self.last_refill = self.clock.now

    def try_take(self, cost: int) -> float:
        """Take `cost` tokens; return 0 on success or seconds to wait if denied."""
        self._refill()
        if self.tokens >= cost:
            self.tokens -= cost
            return 0.0
        missing = cost - self.tokens
        return missing / self.refill_per_second


@dataclass
class Response:
    status: int
    contacts_looked_up: int = 0
    retry_after_seconds: int = 0
    error: str = ""


def make_bucket(clock: FakeClock) -> TokenBucket:
    """One bucket per API key: 100 tokens, refilled at 100 per minute."""
    return TokenBucket(clock, BUCKET_CAPACITY, REQUESTS_PER_MINUTE / SECONDS_PER_MINUTE)


def denied(wait_seconds: float) -> Response:
    """429 with a whole-second Retry-After, rounded up so retrying then succeeds."""
    return Response(HTTP_TOO_MANY_REQUESTS, retry_after_seconds=math.ceil(wait_seconds))


def naive_batch_get(bucket: TokenBucket, contact_ids: list[str]) -> Response:
    """Original handler: every call costs one token, whatever its size."""
    if len(contact_ids) > MAX_BATCH_SIZE:
        return Response(HTTP_BAD_REQUEST, error=f"at most {MAX_BATCH_SIZE} ids per call")
    wait_seconds = bucket.try_take(1)
    if wait_seconds > 0:
        return denied(wait_seconds)
    return Response(HTTP_OK, contacts_looked_up=len(contact_ids))


def batch_cost(contact_ids: list[str]) -> int:
    """Tokens a call costs: one per id, but never zero (an empty call still costs work)."""
    return max(MIN_COST_PER_CALL, len(contact_ids))


def batch_get(bucket: TokenBucket, contact_ids: list[str]) -> Response:
    """Fixed handler: charge one token per contact id looked up."""
    if len(contact_ids) > MAX_BATCH_SIZE:
        return Response(HTTP_BAD_REQUEST, error=f"at most {MAX_BATCH_SIZE} ids per call")
    cost = batch_cost(contact_ids)
    # A call costing more than the bucket can ever hold would get 429 forever;
    # tell the client to split it instead of retrying the same request.
    if cost > bucket.capacity:
        return Response(HTTP_BAD_REQUEST, error="batch larger than the rate limit allows")
    wait_seconds = bucket.try_take(cost)
    if wait_seconds > 0:
        return denied(wait_seconds)
    return Response(HTTP_OK, contacts_looked_up=len(contact_ids))


def single_get(bucket: TokenBucket, contact_id: str) -> Response:
    """GET /contacts/{id}: one lookup, one token, same bucket as the batch endpoint."""
    return batch_get(bucket, [contact_id])


BatchHandler = Callable[[TokenBucket, list[str]], Response]


def simulate_sync_script(handler: BatchHandler, batch_size: int, calls_per_second: int,
                         seconds: int) -> int:
    """Run a client that sends `calls_per_second` full batches; return lookups done."""
    clock = FakeClock()
    bucket = make_bucket(clock)
    contact_ids = [f"contact-{index}" for index in range(batch_size)]
    looked_up = 0
    for _ in range(seconds):
        for _ in range(calls_per_second):
            looked_up += handler(bucket, contact_ids).contacts_looked_up
        clock.advance(1.0)
    return looked_up
