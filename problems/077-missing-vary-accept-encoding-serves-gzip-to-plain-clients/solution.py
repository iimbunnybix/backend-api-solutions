"""A shared cache served gzip bytes to clients that never asked for gzip.

The origin compresses a response when the request says
``Accept-Encoding: gzip``, but it did not send ``Vary: Accept-Encoding``. The
CDN therefore stored the compressed copy under the URL alone and handed it to
every later client, including old devices and health checkers that cannot
decompress it. The fix has two halves: the origin declares
``Vary: Accept-Encoding``, and the cache adds the normalised value of every
varied request header to its key. Accept-Encoding is reduced to just "gzip"
or "identity", so the dozens of browser spellings share two cache entries.
"""
from __future__ import annotations

import gzip
import json
from dataclasses import dataclass, field

GZIP = "gzip"
IDENTITY = "identity"
ACCEPT_ENCODING = "accept-encoding"
CONTENT_ENCODING = "content-encoding"
VARY = "vary"

Headers = dict[str, str]          # header names are always stored lower-case
CacheKey = tuple[str, tuple[tuple[str, str], ...]]

FIRMWARE_MANIFEST = {"model": "thermo-2", "latest": "4.1.7", "channel": "stable",
                     "notes": "Fixes schedule drift. " * 20}


@dataclass(frozen=True)
class Response:
    status: int
    headers: Headers
    body: bytes


def lower_keys(headers: Headers) -> Headers:
    """Copy of ``headers`` with lower-case names (HTTP names are case-insensitive)."""
    lowered = {}
    for name, value in headers.items():
        lowered[name.lower()] = value
    return lowered


def normalise_accept_encoding(value: str | None) -> str:
    """Reduce an Accept-Encoding header to the one choice the origin makes.

    The origin only ever produces gzip or plain bytes, so "gzip, deflate, br"
    and "br;q=1.0, gzip;q=0.8" lead to the same response and must share a key.
    "gzip;q=0" is an explicit refusal, and "*" accepts any encoding.
    """
    if not value:
        return IDENTITY
    for part in value.split(","):
        pieces = part.strip().split(";")
        coding = pieces[0].strip().lower()
        if coding not in (GZIP, "x-gzip", "*"):
            continue
        quality = 1.0
        for parameter in pieces[1:]:
            name, _, number = parameter.strip().partition("=")
            if name.strip().lower() == "q":
                quality = float(number or "0")
        if quality > 0:
            return GZIP
    return IDENTITY


# ---------------------------------------------------------------- origin
@dataclass
class Origin:
    """The API server. Compresses when the client accepts gzip."""

    send_vary: bool
    requests_served: int = 0

    def handle(self, path: str, request_headers: Headers) -> Response:
        """Build the firmware manifest, compressed if the client accepts gzip."""
        self.requests_served += 1
        body = json.dumps(FIRMWARE_MANIFEST).encode("utf-8")
        headers: Headers = {"content-type": "application/json"}
        # The bug: without this header no cache can know the body depends on
        # the request's Accept-Encoding.
        if self.send_vary:
            headers[VARY] = "Accept-Encoding"
        if normalise_accept_encoding(request_headers.get(ACCEPT_ENCODING)) == GZIP:
            body = gzip.compress(body, mtime=0)
            headers[CONTENT_ENCODING] = GZIP
        return Response(200, headers, body)


def naive_origin() -> Origin:
    """The original origin: compresses, but never says ``Vary``."""
    return Origin(send_vary=False)


def fixed_origin() -> Origin:
    """The fixed origin: compresses and declares ``Vary: Accept-Encoding``."""
    return Origin(send_vary=True)


# ---------------------------------------------------------------- caches
@dataclass
class OriginalUrlOnlyCache:
    """The original CDN behaviour: one stored response per URL, ``Vary`` ignored."""

    origin: Origin
    entries: dict[str, Response] = field(default_factory=dict)

    def get(self, path: str, request_headers: Headers) -> Response:
        """Return the cached response for ``path``, fetching it on a miss."""
        headers = lower_keys(request_headers)
        if path not in self.entries:
            self.entries[path] = self.origin.handle(path, headers)
        return self.entries[path]


def varied_header_names(response: Response) -> list[str]:
    """Lower-case header names listed in the response's ``Vary`` header."""
    names = []
    for name in response.headers.get(VARY, "").split(","):
        if name.strip():
            names.append(name.strip().lower())
    return sorted(names)


def normalised_header_value(name: str, value: str | None) -> str:
    """Value of a varied request header as it goes into the cache key."""
    if name == ACCEPT_ENCODING:
        return normalise_accept_encoding(value)
    # Other headers: trim and lower-case so trivial spelling differences match.
    return (value or "").strip().lower()


@dataclass
class VaryAwareCache:
    """A shared cache that keys entries by URL plus every varied request header."""

    origin: Origin
    entries: dict[CacheKey, Response] = field(default_factory=dict)
    # Which request headers each URL varies on, learned from the origin's Vary.
    vary_by_path: dict[str, list[str]] = field(default_factory=dict)

    def key_for(self, path: str, headers: Headers) -> CacheKey:
        """Cache key: the path plus normalised values of the varied headers."""
        parts = []
        for name in self.vary_by_path.get(path, []):
            parts.append((name, normalised_header_value(name, headers.get(name))))
        return (path, tuple(parts))

    def get(self, path: str, request_headers: Headers) -> Response:
        """Return a cached response that matches this request, fetching on a miss."""
        headers = lower_keys(request_headers)
        key = self.key_for(path, headers)
        if key in self.entries:
            return self.entries[key]
        response = self.origin.handle(path, headers)
        # Learn the Vary list first, then recompute the key so the entry is
        # stored under the variant this request actually produced.
        self.vary_by_path[path] = varied_header_names(response)
        self.entries[self.key_for(path, headers)] = response
        return response


# ---------------------------------------------------------------- clients
def plain_client_parse(response: Response) -> dict:
    """What the old thermostat firmware does: parse the body as JSON directly.

    It never sends Accept-Encoding and has no gzip decoder, so compressed
    bytes make this raise (UnicodeDecodeError or json.JSONDecodeError).
    """
    return json.loads(response.body.decode("utf-8"))


def modern_client_parse(response: Response) -> dict:
    """What a browser or current SDK does: decompress if told to, then parse."""
    body = response.body
    if response.headers.get(CONTENT_ENCODING) == GZIP:
        body = gzip.decompress(body)
    return json.loads(body.decode("utf-8"))
