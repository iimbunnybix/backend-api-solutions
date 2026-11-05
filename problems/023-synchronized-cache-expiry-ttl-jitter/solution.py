"""Synchronized cache expiry: every key warmed at deploy expires in the same second.

The product service warms its cache right after each deploy, writing every
product with the same fixed TTL. All of those entries therefore expire at the
same instant, every read misses at once, and the database receives one load
per key in a single second. Because the reloaded entries again share one TTL,
the spike repeats every TTL period. This module adds random jitter to each
TTL, so expiries (and therefore database loads) are spread over a window.
"""
from __future__ import annotations

import random
from collections import Counter
from dataclasses import dataclass
from typing import Callable

BASE_TTL_SECONDS = 300.0
# +/-10% spreads a 300 s TTL over a 60 s window (270 s .. 330 s).
DEFAULT_JITTER_FRACTION = 0.10
# Never hand out a TTL this short, even with a tiny base TTL and big jitter.
MINIMUM_TTL_SECONDS = 1.0

TtlPolicy = Callable[[float], float]


class FakeClock:
    """A clock the tests advance by hand, so expiry is deterministic."""

    def __init__(self, start: float = 0.0) -> None:
        self.now = start

    def time(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def naive_ttl(base_ttl_seconds: float) -> float:
    """The original policy: every entry gets exactly the same TTL."""
    return base_ttl_seconds


def make_jittered_ttl(rng: random.Random,
                      jitter_fraction: float = DEFAULT_JITTER_FRACTION) -> TtlPolicy:
    """Build a TTL policy that picks a uniform TTL in base*(1 -/+ jitter_fraction).

    The random generator is passed in so tests can seed it.
    """
    if not 0.0 <= jitter_fraction < 1.0:
        raise ValueError("jitter_fraction must be in [0, 1)")

    def jittered_ttl(base_ttl_seconds: float) -> float:
        low = base_ttl_seconds * (1.0 - jitter_fraction)
        high = base_ttl_seconds * (1.0 + jitter_fraction)
        return max(MINIMUM_TTL_SECONDS, rng.uniform(low, high))

    return jittered_ttl


@dataclass
class CacheEntry:
    value: object
    expires_at: float


class TtlCache:
    """A read-through cache whose TTL for each write comes from a policy."""

    def __init__(self, clock: FakeClock, ttl_policy: TtlPolicy,
                 base_ttl_seconds: float = BASE_TTL_SECONDS) -> None:
        self._clock = clock
        self._ttl_policy = ttl_policy
        self._base_ttl = base_ttl_seconds
        self._entries: dict[str, CacheEntry] = {}

    def put(self, key: str, value: object) -> None:
        """Store a value with a TTL chosen by the policy."""
        ttl = self._ttl_policy(self._base_ttl)
        self._entries[key] = CacheEntry(value, self._clock.time() + ttl)

    def get_or_load(self, key: str, loader: Callable[[str], object]) -> object:
        """Return the cached value, calling ``loader`` (the database) on a miss."""
        entry = self._entries.get(key)
        if entry is not None and self._clock.time() < entry.expires_at:
            return entry.value
        value = loader(key)
        self.put(key, value)
        return value


class CountingDatabase:
    """Fake database that records how many loads happen in each whole second."""

    def __init__(self, clock: FakeClock) -> None:
        self._clock = clock
        self.loads_per_second: Counter[int] = Counter()

    def load(self, key: str) -> str:
        self.loads_per_second[int(self._clock.time())] += 1
        return f"row-for-{key}"

    def peak_loads_in_one_second(self) -> int:
        if not self.loads_per_second:
            return 0
        return max(self.loads_per_second.values())


def simulate_hot_keys(ttl_policy: TtlPolicy, key_count: int,
                      duration_seconds: int) -> CountingDatabase:
    """Warm ``key_count`` keys at t=0, then read every key once per second.

    Reading every key every second models hot keys: a key is reloaded in the
    first second after it expires, so loads-per-second equals expiries-per-second.
    """
    clock = FakeClock()
    cache = TtlCache(clock, ttl_policy)
    database = CountingDatabase(clock)
    keys = [f"product:{index}" for index in range(key_count)]
    # Deploy-time warm-up writes straight to the cache, not through the loader.
    for key in keys:
        cache.put(key, f"warm-{key}")
    for _second in range(duration_seconds):
        clock.advance(1.0)
        for key in keys:
            cache.get_or_load(key, database.load)
    return database
