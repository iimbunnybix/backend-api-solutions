"""Build a canonical response-cache key so equal queries share one cache entry.

The product-listing API cached responses under the raw query string, so
?page=2&sort=price and ?sort=price&page=2, a stray trailing "&", "%2C" versus
"%2c", or a different utm_source tracking tag each became a separate cache
entry. Almost every request missed and went to the database.

The fix parses the query, drops tracking parameters that never change the
response, sorts parameters by name (keeping repeated values in their original
order, because that order can be meaningful), and re-encodes everything the
same way before using it as the cache key.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable
from urllib.parse import parse_qsl, quote

# Parameters added by marketing links and ad platforms. The API never reads
# them, so they must not split the cache.
IGNORED_PARAM_PREFIXES = ("utm_",)
IGNORED_PARAM_NAMES = frozenset({"fbclid", "gclid", "msclkid", "_ga"})

# RFC 3986 unreserved characters; everything else is percent-encoded.
UNRESERVED_CHARACTERS = "-._~"

CacheKeyFunction = Callable[[str, str], str]


def naive_cache_key(path: str, raw_query: str) -> str:
    """Original key: the path plus the query string exactly as the client sent it."""
    return f"{path}?{raw_query}"


def is_ignored_param(name: str) -> bool:
    """True for tracking parameters that never change the response."""
    if name in IGNORED_PARAM_NAMES:
        return True
    for prefix in IGNORED_PARAM_PREFIXES:
        if name.startswith(prefix):
            return True
    return False


def encode_component(text: str) -> str:
    """Percent-encode one name or value the same way every time (upper-case hex)."""
    return quote(text, safe=UNRESERVED_CHARACTERS)


def canonical_params(raw_query: str) -> list[tuple[str, str]]:
    """Decoded (name, value) pairs without tracking params, sorted by name.

    parse_qsl decodes "%2C", "%2c" and "," to the same text and "+" to a
    space, and skips empty segments such as a trailing "&" or "&&".
    """
    pairs = parse_qsl(raw_query, keep_blank_values=True)
    kept = []
    for name, value in pairs:
        if not is_ignored_param(name):
            kept.append((name, value))
    # sorted() is stable and we sort by name only, so repeated values such as
    # sort=price&sort=rating keep their relative order (it is the tie-breaker).
    return sorted(kept, key=lambda pair: pair[0])


def canonical_cache_key(path: str, raw_query: str) -> str:
    """Cache key that is identical for every spelling of the same request."""
    encoded_pairs = []
    for name, value in canonical_params(raw_query):
        encoded_pairs.append(f"{encode_component(name)}={encode_component(value)}")
    return f"{path}?{'&'.join(encoded_pairs)}"


@dataclass
class ResponseCache:
    """In-memory response cache that counts how often the backend is called."""

    key_function: CacheKeyFunction
    entries: dict[str, str] = field(default_factory=dict)
    hits: int = 0
    backend_calls: int = 0

    def get(self, path: str, raw_query: str, load: Callable[[], str]) -> str:
        """Return the cached response, calling load() only on a miss."""
        key = self.key_function(path, raw_query)
        if key in self.entries:
            self.hits += 1
            return self.entries[key]
        self.backend_calls += 1
        response = load()
        self.entries[key] = response
        return response

    def hit_rate(self) -> float:
        """Share of requests answered from the cache, between 0.0 and 1.0."""
        total = self.hits + self.backend_calls
        if total == 0:
            return 0.0
        return self.hits / total


def replay_traffic(key_function: CacheKeyFunction, path: str, raw_queries: list[str]) -> ResponseCache:
    """Send every query through a fresh cache and return it with its counters."""
    cache = ResponseCache(key_function)
    for raw_query in raw_queries:
        cache.get(path, raw_query, lambda: f"listing for {raw_query}")
    return cache
