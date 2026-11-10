"""Negative caching: stop lookups for missing IDs from bypassing the cache.

A product API caches every product it finds, but when the database returns
"not found" nothing is cached. A scraper cycling through product IDs that do
not exist therefore missed the cache on every request and sent each one to
the primary database.

The fix caches the *absence* too: a "not found" result is stored as a special
marker with a short TTL, so repeat lookups are answered from the cache. When
a product is created, its key is invalidated so the new product shows up at
once instead of waiting for the marker to expire. Each delete also bumps a
per-key invalidation counter, and a reader only writes the marker if the
counter has not moved since it started, so a read that overlaps a create
cannot re-cache "not found" after the create's invalidation.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

FOUND_TTL_SECONDS = 600.0
NOT_FOUND_TTL_SECONDS = 60.0


class FakeClock:
    """A clock the tests move forward by hand."""

    def __init__(self) -> None:
        self.now = 0.0

    def advance(self, seconds: float) -> None:
        """Move time forward."""
        self.now += seconds


class ProductDatabase:
    """In-memory stand-in for the primary database that counts queries."""

    def __init__(self, products: dict[str, dict]) -> None:
        self.products = dict(products)
        self.query_count = 0

    def find_product(self, product_id: str) -> dict | None:
        """Return the product, or None when it does not exist."""
        self.query_count += 1
        return self.products.get(product_id)

    def insert_product(self, product_id: str, product: dict) -> None:
        """Create a product."""
        self.products[product_id] = product


class _NotFoundMarker:
    """Cached value meaning "we asked the database and it had nothing"."""

    def __repr__(self) -> str:
        return "NOT_FOUND"


# A single shared object so `value is NOT_FOUND` can tell "cached absence"
# apart from "nothing in the cache" (which is None).
NOT_FOUND = _NotFoundMarker()


@dataclass
class _CacheEntry:
    value: object
    expires_at: float


class TTLCache:
    """Minimal key-value cache with per-entry expiry."""

    def __init__(self, clock: FakeClock) -> None:
        self.clock = clock
        self.entries: dict[str, _CacheEntry] = {}
        # How many times each key has been invalidated. Readers compare it
        # before and after their database query to detect an overlapping write.
        self.invalidations: dict[str, int] = {}

    def get(self, key: str) -> object | None:
        """Return the cached value, or None if missing or expired."""
        entry = self.entries.get(key)
        if entry is None:
            return None
        if entry.expires_at <= self.clock.now:
            del self.entries[key]
            return None
        return entry.value

    def set(self, key: str, value: object, ttl_seconds: float) -> None:
        """Store a value that expires after ``ttl_seconds``."""
        self.entries[key] = _CacheEntry(value, self.clock.now + ttl_seconds)

    def delete(self, key: str) -> None:
        """Remove a key if present and record that it was invalidated."""
        self.entries.pop(key, None)
        self.invalidations[key] = self.invalidations.get(key, 0) + 1

    def invalidation_count(self, key: str) -> int:
        """Return how many times ``key`` has been invalidated so far."""
        return self.invalidations.get(key, 0)

    def set_unless_invalidated(self, key: str, value: object, ttl_seconds: float,
                               invalidations_seen: int) -> None:
        """Store a value only if the key was not invalidated since the read began.

        In a real cache this check-and-set must be atomic (for example a Lua
        script in Redis, or a memcached lease).
        """
        if self.invalidation_count(key) != invalidations_seen:
            return
        self.set(key, value, ttl_seconds)


def naive_get_product(cache: TTLCache, database: ProductDatabase,
                      product_id: str) -> dict | None:
    """Original read path: only products that exist are ever cached."""
    cached = cache.get(product_id)
    if cached is not None:
        return cached  # type: ignore[return-value]
    product = database.find_product(product_id)
    if product is not None:
        cache.set(product_id, product, FOUND_TTL_SECONDS)
    return product


def get_product(cache: TTLCache, database: ProductDatabase,
                product_id: str) -> dict | None:
    """Fixed read path: "not found" is cached too, for a shorter time."""
    cached = cache.get(product_id)
    if cached is NOT_FOUND:
        return None
    if cached is not None:
        return cached  # type: ignore[return-value]
    # Remember the invalidation count *before* querying: if a create commits
    # and invalidates while we query, our "not found" is already stale.
    invalidations_seen = cache.invalidation_count(product_id)
    product = database.find_product(product_id)
    if product is None:
        # Short TTL: if the product is created by a path that forgets to
        # invalidate, it still becomes visible within a minute.
        cache.set_unless_invalidated(product_id, NOT_FOUND, NOT_FOUND_TTL_SECONDS,
                                     invalidations_seen)
        return None
    cache.set(product_id, product, FOUND_TTL_SECONDS)
    return product


def create_product(cache: TTLCache, database: ProductDatabase,
                   product_id: str, product: dict) -> None:
    """Write path: insert, then drop any cached "not found" for this key."""
    database.insert_product(product_id, product)
    cache.delete(product_id)


ReadPath = Callable[["TTLCache", "ProductDatabase", str], "dict | None"]


def simulate_scraper(read_path: ReadPath, cache: TTLCache, database: ProductDatabase,
                     missing_ids: list[str], rounds: int,
                     seconds_between_rounds: float) -> int:
    """Request every missing ID once per round; return database queries made."""
    for _ in range(rounds):
        for product_id in missing_ids:
            read_path(cache, database, product_id)
        cache.clock.advance(seconds_between_rounds)
    return database.query_count
