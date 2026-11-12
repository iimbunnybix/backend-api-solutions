"""Reverse-proxy cache that does not keep serving errors after the origin recovers.

A catalog API set `Cache-Control: public, max-age=300` on every response from
one global middleware, including 503s. During a 30-second origin blip, every
URL whose cached copy happened to expire got a 503 stored for five minutes, so
those pages stayed broken long after the origin was healthy again. The fix:
the origin marks only cacheable statuses as cacheable and sends `no-store` on
5xx, the proxy caps how long it will ever keep a 5xx, and good responses carry
`stale-if-error` so the proxy serves the last good copy during an outage.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

SUCCESS_MAX_AGE_SECONDS = 300
# How long after expiry a good copy may still be served while the origin errors.
STALE_IF_ERROR_SECONDS = 600
# Even a misconfigured origin cannot make the proxy keep a 5xx longer than this.
ERROR_MAX_AGE_CAP_SECONDS = 5
HTTP_OK = 200
HTTP_SERVICE_UNAVAILABLE = 503
FIRST_SERVER_ERROR = 500
# Statuses that are safe to cache for the normal TTL (RFC 9110 section 15.1
# lists these as heuristically cacheable; 5xx errors are deliberately absent).
CACHEABLE_STATUSES = frozenset({200, 203, 204, 300, 301, 308, 404, 410})


class FakeClock:
    """Controllable clock in whole seconds."""

    def __init__(self, now: int = 0):
        self.now = now


@dataclass
class Response:
    status: int
    body: str
    headers: dict[str, str] = field(default_factory=dict)


class FlakyOrigin:
    """Catalog service that returns 503 for every request during an outage window."""

    def __init__(self, clock: FakeClock, outage_start: int, outage_end: int):
        self.clock = clock
        self.outage_start = outage_start
        self.outage_end = outage_end
        self.calls = 0

    def handle(self, path: str) -> Response:
        """Render the page, or fail while the outage is in progress."""
        self.calls += 1
        if self.outage_start <= self.clock.now < self.outage_end:
            return Response(HTTP_SERVICE_UNAVAILABLE, "upstream unavailable")
        return Response(HTTP_OK, f"page {path}")


def naive_cache_headers(response: Response) -> Response:
    """Original global middleware: the same Cache-Control on every response."""
    response.headers["Cache-Control"] = f"public, max-age={SUCCESS_MAX_AGE_SECONDS}"
    return response


def fixed_cache_headers(response: Response) -> Response:
    """Fixed middleware: only cacheable statuses are cacheable; errors say no-store."""
    if response.status in CACHEABLE_STATUSES:
        response.headers["Cache-Control"] = (
            f"public, max-age={SUCCESS_MAX_AGE_SECONDS}, stale-if-error={STALE_IF_ERROR_SECONDS}"
        )
    else:
        response.headers["Cache-Control"] = "no-store"
    return response


def parse_cache_control(value: str) -> dict[str, int | None]:
    """Turn 'public, max-age=300' into {'public': None, 'max-age': 300}."""
    directives: dict[str, int | None] = {}
    for part in value.split(","):
        name, _, argument = part.strip().partition("=")
        if not name:
            continue
        directives[name.lower()] = int(argument) if argument.isdigit() else None
    return directives


@dataclass
class CacheEntry:
    response: Response
    stored_at: int
    max_age: int
    stale_if_error: int

    def is_fresh(self, now: int) -> bool:
        """True while the entry is within its max-age."""
        return now < self.stored_at + self.max_age

    def may_serve_on_error(self, now: int) -> bool:
        """True while an expired entry may still stand in for an origin error."""
        return now < self.stored_at + self.max_age + self.stale_if_error


Fetch = Callable[[str], Response]


class NaiveProxyCache:
    """Original proxy: stores whatever carries a max-age, whatever its status."""

    def __init__(self, clock: FakeClock, fetch: Fetch):
        self.clock = clock
        self.fetch = fetch
        self.entries: dict[str, CacheEntry] = {}

    def get(self, path: str) -> Response:
        """Serve a fresh entry, otherwise go to the origin and cache its answer."""
        entry = self.entries.get(path)
        if entry is not None and entry.is_fresh(self.clock.now):
            return entry.response
        response = self.fetch(path)
        max_age = parse_cache_control(response.headers.get("Cache-Control", "")).get("max-age")
        if max_age:
            # Bug: a 503 is stored for the full max-age like any other page.
            self.entries[path] = CacheEntry(response, self.clock.now, max_age, 0)
        return response


def storable_entry(response: Response, now: int) -> CacheEntry | None:
    """Decide whether and for how long a response may be stored."""
    directives = parse_cache_control(response.headers.get("Cache-Control", ""))
    max_age = directives.get("max-age")
    if "no-store" in directives or not max_age:
        return None
    if response.status >= FIRST_SERVER_ERROR:
        # Defence in depth: an origin that forgets no-store gets a tiny error TTL
        # (enough to shield it from a stampede) and never stale-if-error.
        return CacheEntry(response, now, min(max_age, ERROR_MAX_AGE_CAP_SECONDS), 0)
    stale_if_error = directives.get("stale-if-error") or 0
    return CacheEntry(response, now, max_age, stale_if_error)


class FixedProxyCache:
    """Proxy that caps error TTLs, honours no-store and serves stale on error."""

    def __init__(self, clock: FakeClock, fetch: Fetch):
        self.clock = clock
        self.fetch = fetch
        self.entries: dict[str, CacheEntry] = {}

    def get(self, path: str) -> Response:
        """Serve fresh, else refetch; on a 5xx fall back to an allowed stale copy."""
        now = self.clock.now
        entry = self.entries.get(path)
        if entry is not None and entry.is_fresh(now):
            return entry.response
        response = self.fetch(path)
        if response.status >= FIRST_SERVER_ERROR and entry is not None and entry.may_serve_on_error(now):
            # Keep the good copy in place: a stale page beats an error page.
            return entry.response
        new_entry = storable_entry(response, now)
        if new_entry is not None:
            self.entries[path] = new_entry
        return response


@dataclass
class OutageReport:
    errors_during_outage: int
    errors_after_recovery: int
    urls_broken_after_recovery: int
    last_error_second: int | None


def simulate_outage(middleware: Callable[[Response], Response], proxy_class: type,
                    url_count: int = 300, outage_start: int = 600,
                    outage_seconds: int = 30, duration: int = 1200) -> OutageReport:
    """Every URL is requested once per second; URL i is first requested at second i.

    Staggered first requests mean cached copies expire at different seconds, as
    in production, so only the URLs that expire during the outage are hit.
    """
    clock = FakeClock()
    outage_end = outage_start + outage_seconds
    origin = FlakyOrigin(clock, outage_start, outage_end)

    def fetch(path: str) -> Response:
        return middleware(origin.handle(path))

    proxy = proxy_class(clock, fetch)
    report = OutageReport(0, 0, 0, None)
    broken_urls: set[int] = set()
    for second in range(duration):
        clock.now = second
        for url_index in range(min(second + 1, url_count)):
            status = proxy.get(f"/products/{url_index}").status
            if status < FIRST_SERVER_ERROR:
                continue
            report.last_error_second = second
            if second < outage_end:
                report.errors_during_outage += 1
            else:
                report.errors_after_recovery += 1
                broken_urls.add(url_index)
    report.urls_broken_after_recovery = len(broken_urls)
    return report
