"""Conditional GET that still returns 304 after the CDN weakens the ETag.

The catalog API sent a strong ETag such as ``"v42"`` and answered 304 only
when ``If-None-Match`` was exactly the same string. The CDN compresses
responses on the fly and, as nginx and most CDNs do, turns a strong ETag into a
weak one (``W/"v42"``) because the compressed bytes differ. Clients then sent
``If-None-Match: W/"v42"``, the exact string compare never matched, and every
revalidation downloaded the full body again.

The fix parses ``If-None-Match`` as RFC 9110 says: a comma-separated list of
entity tags or ``*``, compared with the *weak* comparison function, which
ignores the ``W/`` prefix.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

STATUS_OK = 200
STATUS_NOT_MODIFIED = 304

WEAK_PREFIX = "W/"
ANY_TAG = "*"


@dataclass(frozen=True)
class EntityTag:
    """One parsed entity tag: the quoted value and whether it is weak."""
    opaque: str
    weak: bool


@dataclass
class Response:
    """A minimal HTTP response."""
    status: int
    etag: str
    body: bytes


def parse_entity_tag(text: str) -> EntityTag | None:
    """Parse ``"abc"`` or ``W/"abc"``; return None for anything malformed."""
    text = text.strip()
    weak = False
    if text.startswith(WEAK_PREFIX):
        weak = True
        text = text[len(WEAK_PREFIX):]
    # An entity tag must be a quoted string; unquoted values are ignored
    # rather than matched, so garbage never produces a false 304.
    if len(text) < 2 or not (text.startswith('"') and text.endswith('"')):
        return None
    return EntityTag(opaque=text, weak=weak)


def parse_if_none_match(header: str) -> list[EntityTag]:
    """Split an If-None-Match list such as ``W/"a", "b"`` into entity tags.

    Entity-tag values cannot contain a double quote, so splitting on commas
    is safe for every valid header.
    """
    tags: list[EntityTag] = []
    for part in header.split(","):
        tag = parse_entity_tag(part)
        if tag is not None:
            tags.append(tag)
    return tags


def weak_match(first: EntityTag, second: EntityTag) -> bool:
    """RFC 9110 weak comparison: equal quoted values, W/ prefix ignored."""
    return first.opaque == second.opaque


def naive_is_not_modified(if_none_match: str | None, current_etag: str) -> bool:
    """Original version: exact string comparison of the whole header."""
    return if_none_match == current_etag


def is_not_modified(if_none_match: str | None, current_etag: str) -> bool:
    """Decide whether a GET can be answered with 304 Not Modified."""
    if if_none_match is None:
        return False
    if if_none_match.strip() == ANY_TAG:
        # "*" matches whenever the resource exists, which it does here.
        return True
    current = parse_entity_tag(current_etag)
    if current is None:
        return False
    for candidate in parse_if_none_match(if_none_match):
        if weak_match(candidate, current):
            return True
    return False


def serve_product(body: bytes, etag: str, if_none_match: str | None,
                  check: Callable[[str | None, str], bool] = is_not_modified) -> Response:
    """Return 304 with no body when the client's copy is current, else 200."""
    if check(if_none_match, etag):
        return Response(STATUS_NOT_MODIFIED, etag, b"")
    return Response(STATUS_OK, etag, body)


def cdn_weaken(etag: str) -> str:
    """What a compressing CDN does to a strong ETag before passing it on."""
    if etag.startswith(WEAK_PREFIX):
        return etag
    return WEAK_PREFIX + etag
