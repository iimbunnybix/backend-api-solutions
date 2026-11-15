"""Download endpoint that honours If-Range so resumed downloads cannot splice two files.

Clients resume an interrupted download by asking for the rest of the file with
"Range: bytes=N-" and sending the ETag they first received in "If-Range". The
original server ignored If-Range and always served the requested byte range of
whatever file was current. When a new release replaced the file between the two
requests, clients glued the start of the old file to the end of the new one and
got a corrupt installer. This version serves the range only when If-Range
matches the current strong ETag; otherwise it sends the whole new file (200).
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Callable

STATUS_OK = 200
STATUS_PARTIAL_CONTENT = 206
STATUS_RANGE_NOT_SATISFIABLE = 416

# One range only, "bytes=START-" or "bytes=START-END"; multi-range is not supported.
# ASCII digits only (Python's \d also matches other scripts), and at most 19 of
# them so int() never sees a giant number.
SINGLE_RANGE_PATTERN = re.compile(r"bytes=(\d{1,19})-(\d{0,19})", re.ASCII)
WEAK_ETAG_PREFIX = "W/"


@dataclass
class Response:
    """A minimal HTTP response: status code, headers and body bytes."""

    status: int
    headers: dict[str, str] = field(default_factory=dict)
    body: bytes = b""


# A request handler: (file store, request headers) -> response.
Handler = Callable[["FileStore", dict[str, str]], Response]


class FileStore:
    """Holds the current version of one downloadable file."""

    def __init__(self, content: bytes):
        self.content = content

    def replace(self, new_content: bytes) -> None:
        """Publish a new release under the same URL."""
        self.content = new_content

    def etag(self) -> str:
        """Strong ETag derived from the bytes, so any change gives a new tag."""
        return '"' + hashlib.sha256(self.content).hexdigest()[:16] + '"'


def parse_range(range_header: str, size: int) -> tuple[int, int] | None:
    """Return (first, last) byte positions, inclusive, or None if unparseable.

    Invalid Range headers must be ignored (RFC 9110), which means a full 200.
    """
    match = SINGLE_RANGE_PATTERN.fullmatch(range_header.strip())
    if match is None:
        return None
    first = int(match.group(1))
    if not match.group(2):
        return first, size - 1
    last = int(match.group(2))
    if last < first:
        return None
    # A last position past the end just means "to the end".
    return first, min(last, size - 1)


def _full_response(store: FileStore) -> Response:
    """200 with the complete current file."""
    headers = {"ETag": store.etag(), "Content-Length": str(len(store.content)), "Accept-Ranges": "bytes"}
    return Response(STATUS_OK, headers, store.content)


def _partial_response(store: FileStore, first: int, last: int) -> Response:
    """206 with bytes first..last and a Content-Range describing them."""
    size = len(store.content)
    body = store.content[first:last + 1]
    headers = {
        "ETag": store.etag(),
        "Content-Range": f"bytes {first}-{last}/{size}",
        "Content-Length": str(len(body)),
    }
    return Response(STATUS_PARTIAL_CONTENT, headers, body)


def _unsatisfiable_response(store: FileStore) -> Response:
    """416 for a range that starts at or after the end of the file."""
    return Response(STATUS_RANGE_NOT_SATISFIABLE, {"Content-Range": f"bytes */{len(store.content)}"})


def naive_handle_get(store: FileStore, request_headers: dict[str, str]) -> Response:
    """Original handler: serves any requested range of the *current* file."""
    range_header = request_headers.get("Range")
    if range_header is None:
        return _full_response(store)
    return _serve_range(store, range_header)


def _serve_range(store: FileStore, range_header: str) -> Response:
    """Answer a Range request against the current file (no If-Range logic)."""
    byte_range = parse_range(range_header, len(store.content))
    if byte_range is None:
        return _full_response(store)
    first, last = byte_range
    if first >= len(store.content):
        return _unsatisfiable_response(store)
    return _partial_response(store, first, last)


def if_range_matches(if_range: str, current_etag: str) -> bool:
    """If-Range needs a *strong* match: weak tags and dates never match here.

    Dates are treated as a mismatch on purpose: one-second timestamps cannot
    tell apart two releases published within the same second.
    """
    if if_range.startswith(WEAK_ETAG_PREFIX):
        return False
    return if_range == current_etag


def handle_get(store: FileStore, request_headers: dict[str, str]) -> Response:
    """Fixed handler: a range is served only if the client still has the same file."""
    range_header = request_headers.get("Range")
    if range_header is None:
        return _full_response(store)
    if_range = request_headers.get("If-Range")
    # The file changed since the client's first request: its partial copy is
    # useless, so send the whole new file instead of the tail of it.
    if if_range is not None and not if_range_matches(if_range, store.etag()):
        return _full_response(store)
    return _serve_range(store, range_header)


def resume_download(handler: Handler, store: FileStore, partial: bytes, etag: str) -> bytes:
    """Client side: fetch the rest of a file, sending If-Range with the saved ETag.

    A 206 is appended to what we already have; a 200 means the server sent a
    fresh full file, so the partial copy is thrown away.
    """
    headers = {"Range": f"bytes={len(partial)}-", "If-Range": etag}
    response = handler(store, headers)
    if response.status == STATUS_PARTIAL_CONTENT:
        return partial + response.body
    if response.status == STATUS_OK:
        return response.body
    raise ValueError(f"cannot resume download: HTTP {response.status}")
