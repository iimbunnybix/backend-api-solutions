"""Price lookups cached with functools.lru_cache on a per-request method.

The checkout API built a new ``PricingService`` for every request and put
``@functools.lru_cache`` on its ``price_for(self, sku)`` method to save calls
to the slow catalog service. The cache key includes ``self``, so a new request
never hits an entry made by an earlier one (the catalog saw no fewer calls),
and the cache holds a strong reference to every service object, with its
request body, forever. Pods grew in memory until they were OOMKilled.

The fix keeps one app-wide cache keyed only by SKU, with a TTL so prices
refresh and a maximum size so memory is bounded, and passes it into the
short-lived per-request service, which is then free to be garbage-collected.
"""
from __future__ import annotations

import functools
from collections import OrderedDict
from dataclasses import dataclass

PRICE_TTL_SECONDS = 60.0
MAX_CACHED_PRICES = 10_000


class FakeClock:
    """Manually advanced clock, in seconds."""

    def __init__(self) -> None:
        self.now = 0.0

    def advance(self, seconds: float) -> None:
        self.now += seconds


class CatalogClient:
    """Stands in for the slow catalog HTTP service; counts how often it is called."""

    def __init__(self, prices: dict[str, int]) -> None:
        self.prices = prices
        self.calls = 0

    def fetch_price_cents(self, sku: str) -> int:
        self.calls += 1
        return self.prices[sku]


@dataclass
class Request:
    """The parts of an HTTP request the service keeps a reference to."""

    request_id: str
    body: bytes


# --- the original, buggy version ---------------------------------------------
class NaivePricingService:
    """Created once per request. The cache below is shared by ALL instances."""

    def __init__(self, request: Request, catalog: CatalogClient) -> None:
        self.request = request
        self.catalog = catalog

    # WHY this is the bug: lru_cache lives on the class's function, and its key
    # is (self, sku). Every request has a new self, so nothing is reused, and
    # the cache keeps every self (and its request body) alive.
    @functools.lru_cache(maxsize=None)
    def price_for(self, sku: str) -> int:
        return self.catalog.fetch_price_cents(sku)

    def cart_total_cents(self, skus: list[str]) -> int:
        total = 0
        for sku in skus:
            total += self.price_for(sku)
        return total


# --- the fix ------------------------------------------------------------------
class SharedPriceCache:
    """App-wide cache keyed by SKU only, with a TTL and a size limit (LRU eviction)."""

    def __init__(self, catalog: CatalogClient, clock: FakeClock,
                 ttl_seconds: float = PRICE_TTL_SECONDS,
                 max_entries: int = MAX_CACHED_PRICES) -> None:
        self.catalog = catalog
        self.clock = clock
        self.ttl_seconds = ttl_seconds
        self.max_entries = max_entries
        # sku -> (price, time it was fetched); order = least recently used first
        self._entries: OrderedDict[str, tuple[int, float]] = OrderedDict()

    def __len__(self) -> int:
        return len(self._entries)

    def price_for(self, sku: str) -> int:
        """Return a cached price if it is younger than the TTL, else fetch it."""
        cached = self._entries.get(sku)
        if cached is not None:
            price, fetched_at = cached
            if self.clock.now - fetched_at < self.ttl_seconds:
                self._entries.move_to_end(sku)
                return price
        price = self.catalog.fetch_price_cents(sku)
        self._store(sku, price)
        return price

    def _store(self, sku: str, price: int) -> None:
        """Insert or refresh one entry, evicting the least recently used if full."""
        self._entries[sku] = (price, self.clock.now)
        self._entries.move_to_end(sku)
        while len(self._entries) > self.max_entries:
            self._entries.popitem(last=False)


class PricingService:
    """Still created per request, but it only borrows the shared cache."""

    def __init__(self, request: Request, price_cache: SharedPriceCache) -> None:
        self.request = request
        self.price_cache = price_cache

    def cart_total_cents(self, skus: list[str]) -> int:
        total = 0
        for sku in skus:
            total += self.price_cache.price_for(sku)
        return total
