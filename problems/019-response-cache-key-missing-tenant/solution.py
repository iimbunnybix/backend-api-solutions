"""Response cache whose key includes every input that changes the response.

A multi-tenant pricing API cached `GET /catalog/prices` responses keyed only by
the URL path. The response actually depends on the tenant (each shop has its
own price list), the Accept-Language header and the currency query parameter,
so whichever request filled the cache first decided what everyone else saw:
shoppers of one tenant were shown another tenant's prices. This version builds
the cache key from an explicit, per-route "vary" list and refuses to cache a
route that has not declared one.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Callable

# Separator that cannot appear in a header name, so "a|b" + "c" never
# collides with "a" + "b|c" once values are length-prefixed below.
KEY_PART_SEPARATOR = "\x1f"


@dataclass(frozen=True)
class Request:
    """The parts of an HTTP request that a handler can read."""

    path: str
    tenant_id: str
    headers: dict[str, str] = field(default_factory=dict)
    query: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class VarySpec:
    """Which request inputs change the response of one route.

    `uses_tenant` is True for almost every route in a multi-tenant system;
    it is explicit so a reviewer sees the decision instead of a default.
    """

    uses_tenant: bool
    headers: tuple[str, ...] = ()
    query_params: tuple[str, ...] = ()


Handler = Callable[[Request], str]


def naive_cache_key(request: Request) -> str:
    """The original key: only the path. Tenant, language and currency are lost."""
    return request.path


def _length_prefixed(value: str) -> str:
    """Encode a value so that no two different lists of values join to the same string."""
    return f"{len(value)}:{value}"


def build_cache_key(request: Request, vary: VarySpec) -> str:
    """Build a key from the path plus every input the route says it depends on."""
    parts = ["path", _length_prefixed(request.path)]
    if vary.uses_tenant:
        parts += ["tenant", _length_prefixed(request.tenant_id)]
    # Header names are case-insensitive in HTTP, so normalise them to lower case
    # both in the spec and in the request before looking them up.
    lower_headers = {name.lower(): value for name, value in request.headers.items()}
    for header_name in sorted(name.lower() for name in vary.headers):
        # A missing header is recorded as missing, not skipped, so
        # "no Accept-Language" and "Accept-Language: en" never share a key.
        value = lower_headers.get(header_name)
        encoded = "-" if value is None else _length_prefixed(value)
        parts += ["h", header_name, encoded]
    for param in sorted(vary.query_params):
        value = request.query.get(param)
        encoded = "-" if value is None else _length_prefixed(value)
        parts += ["q", param, encoded]
    raw_key = KEY_PART_SEPARATOR.join(parts)
    # Hash to keep keys short for the cache backend; the raw key is still
    # unambiguous because every value is length-prefixed.
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def has_ambiguous_varied_header(request: Request, vary: VarySpec) -> bool:
    """True if a header the route varies on is sent more than once in different casings.

    The key builder and the handler could then read different copies of the
    header, so the stored body might not match the key it is stored under.
    """
    varied_names = {name.lower() for name in vary.headers}
    seen_counts: dict[str, int] = {}
    for name in request.headers:
        lower_name = name.lower()
        if lower_name in varied_names:
            seen_counts[lower_name] = seen_counts.get(lower_name, 0) + 1
    for count in seen_counts.values():
        if count > 1:
            return True
    return False


class NaiveResponseCache:
    """The original cache: one entry per path, shared by every tenant."""

    def __init__(self) -> None:
        self.entries: dict[str, str] = {}

    def get_or_compute(self, request: Request, handler: Handler) -> str:
        """Return the cached body for the path, computing it on the first call."""
        key = naive_cache_key(request)
        if key not in self.entries:
            self.entries[key] = handler(request)
        return self.entries[key]


class VaryAwareResponseCache:
    """Cache that only serves a stored response to requests with the same inputs."""

    def __init__(self) -> None:
        self.entries: dict[str, str] = {}
        self.vary_by_route: dict[str, VarySpec] = {}

    def register_route(self, path: str, vary: VarySpec) -> None:
        """Declare what a route's response depends on before it can be cached."""
        self.vary_by_route[path] = vary

    def get_or_compute(self, request: Request, handler: Handler) -> str:
        """Return a cached body for identical inputs, otherwise compute and store it."""
        vary = self.vary_by_route.get(request.path)
        if vary is None:
            # Unknown dependencies: caching would risk leaking data, so don't.
            return handler(request)
        if has_ambiguous_varied_header(request, vary):
            # Never let one ambiguous request decide what other users receive.
            return handler(request)
        key = build_cache_key(request, vary)
        if key not in self.entries:
            self.entries[key] = handler(request)
        return self.entries[key]
