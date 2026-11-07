"""ETags that differ between identical responses, so conditional GETs never hit.

A catalog API computes its ETag by hashing ``json.dumps(body)``. The body
contains a set of tags, and Python randomises the iteration order of a set of
strings per process (``PYTHONHASHSEED``), and attribute dicts are filled in
whatever order their sources answered. The same product therefore serialises
to different bytes on different servers, so clients and the CDN almost never
get a ``304 Not Modified``.

The fix serialises the body canonically (sorted keys, sets turned into sorted
lists, fixed separators), hashes those exact bytes, and sends those same bytes
as the response body, so equal content always means an equal ETag.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

# 16 bytes of SHA-256 (32 hex chars) is plenty to tell versions of one URL apart.
ETAG_HEX_LENGTH = 32
JSON_SEPARATORS = (",", ":")
STATUS_OK = 200
STATUS_NOT_MODIFIED = 304


def _set_to_list(value: Any) -> Any:
    """json.dumps cannot encode sets; the naive code just lists them as-is."""
    if isinstance(value, (set, frozenset)):
        return list(value)
    raise TypeError(f"cannot serialise {type(value).__name__}")


def naive_serialize(body: dict) -> bytes:
    """The original code: key order and set order come from the process."""
    return json.dumps(body, default=_set_to_list).encode("utf-8")


def _canonical(value: Any) -> Any:
    """Rebuild ``value`` so its JSON form does not depend on insertion order."""
    if isinstance(value, dict):
        # Keys are sorted later by sort_keys; only the values need recursion.
        return {key: _canonical(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        # Lists keep their order: in a list, order is part of the content.
        return [_canonical(item) for item in value]
    if isinstance(value, (set, frozenset)):
        # A set has no order, so pick one. Sorting by each element's own
        # canonical JSON works even for mixed types (1, "1", None).
        items = [_canonical(item) for item in value]
        return sorted(items, key=_canonical_text)
    return value


def _canonical_text(value: Any) -> str:
    """Canonical JSON text of an already-canonicalised value."""
    return json.dumps(value, sort_keys=True, separators=JSON_SEPARATORS,
                      ensure_ascii=False, allow_nan=False)


def canonical_serialize(body: dict) -> bytes:
    """The fix: the same content always produces the same bytes."""
    return _canonical_text(_canonical(body)).encode("utf-8")


def strong_etag(payload: bytes) -> str:
    """A strong ETag for the exact bytes that will be sent."""
    digest = hashlib.sha256(payload).hexdigest()[:ETAG_HEX_LENGTH]
    return f'"{digest}"'


def _opaque_tag(tag: str) -> str:
    """Strip the weak prefix: If-None-Match uses the weak comparison."""
    tag = tag.strip()
    if tag.startswith("W/"):
        return tag[2:]
    return tag


def if_none_match_hits(header: str | None, current_etag: str) -> bool:
    """True if the client's cached copy is still current (RFC 9110 13.1.2)."""
    if header is None:
        return False
    if header.strip() == "*":
        return True
    for candidate in header.split(","):
        if _opaque_tag(candidate) == current_etag:
            return True
    return False


def respond(body: dict, if_none_match: str | None,
            serialize=canonical_serialize) -> tuple[int, dict[str, str], bytes]:
    """Answer a GET: 304 with no body if the client's copy is current."""
    payload = serialize(body)
    # Hash the bytes we send, not some other serialisation of the body,
    # otherwise the ETag and the body can disagree.
    etag = strong_etag(payload)
    headers = {"ETag": etag, "Content-Type": "application/json"}
    if if_none_match_hits(if_none_match, etag):
        return STATUS_NOT_MODIFIED, headers, b""
    return STATUS_OK, headers, payload


def naive_respond(body: dict, if_none_match: str | None) -> tuple[int, dict[str, str], bytes]:
    """The original endpoint, for side-by-side comparison."""
    return respond(body, if_none_match, serialize=naive_serialize)
