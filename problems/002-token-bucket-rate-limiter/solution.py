"""Per-client token-bucket rate limiter with Retry-After."""
from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass
from typing import Callable


@dataclass
class Decision:
    allowed: bool
    remaining: int
    retry_after_s: float        # 0 when allowed


class TokenBucketLimiter:
    def __init__(self, rate_per_s: float, capacity: int,
                 clock: Callable[[], float] = time.monotonic, max_clients: int = 100_000):
        if rate_per_s <= 0 or capacity <= 0:
            raise ValueError("rate and capacity must be positive")
        self.rate = rate_per_s
        self.capacity = capacity
        self._clock = clock
        self._max_clients = max_clients
        self._buckets: dict[str, tuple[float, float]] = {}   # client -> (tokens, last_ts)
        self._lock = threading.Lock()

    def allow(self, client_id: str, cost: float = 1.0) -> Decision:
        if cost > self.capacity:
            raise ValueError("cost exceeds bucket capacity; request can never succeed")
        now = self._clock()
        with self._lock:
            tokens, last = self._buckets.get(client_id, (float(self.capacity), now))
            tokens = min(self.capacity, tokens + (now - last) * self.rate)   # lazy refill
            if tokens >= cost:
                tokens -= cost
                self._store(client_id, tokens, now)
                return Decision(True, int(tokens), 0.0)
            self._store(client_id, tokens, now)
            wait = (cost - tokens) / self.rate
            return Decision(False, int(tokens), math.ceil(wait * 1000) / 1000)

    def _store(self, client_id: str, tokens: float, now: float) -> None:
        self._buckets.pop(client_id, None)
        self._buckets[client_id] = (tokens, now)
        if len(self._buckets) > self._max_clients:            # evict least recently used
            self._buckets.pop(next(iter(self._buckets)))


def handle_request(limiter: TokenBucketLimiter, api_key: str) -> tuple[int, dict]:
    """Shape of an HTTP handler using the limiter."""
    d = limiter.allow(api_key)
    headers = {"X-RateLimit-Remaining": str(d.remaining)}
    if not d.allowed:
        headers["Retry-After"] = str(math.ceil(d.retry_after_s))
        return 429, headers
    return 200, headers
