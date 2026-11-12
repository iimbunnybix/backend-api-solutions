"""Request-body reader that refuses oversized uploads instead of running out of memory.

The original handler called `stream.read()` on every request body, so a client
that sent a multi-gigabyte body (by mistake or on purpose) made the worker
buffer all of it in RAM and get OOM-killed, dropping every other in-flight
request on that worker. This version checks Content-Length before reading,
reads chunked bodies in small pieces and stops with 413 as soon as the limit
is crossed, so memory per request is bounded by the limit.
"""
from __future__ import annotations

from dataclasses import dataclass

MAX_BODY_BYTES = 1 * 1024 * 1024        # 1 MiB is plenty for a JSON API
READ_CHUNK_BYTES = 64 * 1024
HTTP_OK = 200
HTTP_BAD_REQUEST = 400
HTTP_PAYLOAD_TOO_LARGE = 413


class BodyRejected(Exception):
    """The body cannot be accepted; carries the HTTP status to return."""

    def __init__(self, status: int, reason: str) -> None:
        super().__init__(reason)
        self.status = status
        self.reason = reason


class FakeClientStream:
    """A socket-like stream that produces `total_bytes` of data on demand.

    It records how many bytes the server actually pulled off the wire, which
    is what ends up in server memory.
    """

    def __init__(self, total_bytes: int) -> None:
        self.remaining = total_bytes
        self.bytes_read = 0

    def read(self, size: int = -1) -> bytes:
        """Return up to `size` bytes (all remaining bytes when size is -1)."""
        if size < 0 or size > self.remaining:
            size = self.remaining
        self.remaining -= size
        self.bytes_read += size
        return b"x" * size


def naive_read_body(stream: FakeClientStream, headers: dict[str, str]) -> bytes:
    """Original version: trust the client and buffer the whole body."""
    return stream.read()


def find_content_length_header(headers: dict[str, str]) -> str | None:
    """Return the Content-Length value, matching the header name in any case.

    Header names are case-insensitive, and HTTP/2 and most proxies send them
    lowercase, so an exact "Content-Length" lookup would miss the header.
    """
    found_values = []
    for name, value in headers.items():
        if name.lower() == "content-length":
            found_values.append(value)
    if not found_values:
        return None
    # Two spellings with different values make the body boundary ambiguous.
    if len(set(found_values)) > 1:
        raise BodyRejected(HTTP_BAD_REQUEST, "conflicting Content-Length headers")
    return found_values[0]


def parse_content_length(headers: dict[str, str]) -> int | None:
    """Return the declared length, None if absent, or reject a malformed value."""
    raw_value = find_content_length_header(headers)
    if raw_value is None:
        return None
    # int() would accept "+5", " 5" and "5_0"; HTTP allows digits only.
    if not raw_value.isascii() or not raw_value.isdigit():
        raise BodyRejected(HTTP_BAD_REQUEST, "invalid Content-Length")
    return int(raw_value)


def read_exactly(stream: FakeClientStream, length: int) -> bytes:
    """Read a body whose length is known and already checked against the limit."""
    chunks = []
    still_needed = length
    while still_needed > 0:
        chunk = stream.read(min(READ_CHUNK_BYTES, still_needed))
        if not chunk:
            raise BodyRejected(HTTP_BAD_REQUEST, "body shorter than Content-Length")
        chunks.append(chunk)
        still_needed -= len(chunk)
    return b"".join(chunks)


def read_until_limit(stream: FakeClientStream, max_bytes: int) -> bytes:
    """Read a body of unknown length (chunked) and stop once it passes the limit."""
    chunks = []
    total = 0
    while True:
        chunk = stream.read(READ_CHUNK_BYTES)
        if not chunk:
            return b"".join(chunks)
        total += len(chunk)
        # Stop at the first chunk over the limit: we never hold more than
        # max_bytes + one chunk, whatever the client keeps sending.
        if total > max_bytes:
            raise BodyRejected(HTTP_PAYLOAD_TOO_LARGE, f"body larger than {max_bytes} bytes")
        chunks.append(chunk)


def read_body(stream: FakeClientStream, headers: dict[str, str],
              max_bytes: int = MAX_BODY_BYTES) -> bytes:
    """Fixed version: bounded memory for every request."""
    declared_length = parse_content_length(headers)
    if declared_length is None:
        return read_until_limit(stream, max_bytes)
    # Reject from the header alone: no body bytes are read for a huge upload.
    if declared_length > max_bytes:
        raise BodyRejected(HTTP_PAYLOAD_TOO_LARGE, f"body larger than {max_bytes} bytes")
    # Read only what was declared; extra bytes belong to the next request.
    return read_exactly(stream, declared_length)


@dataclass
class Response:
    status: int
    body: str


def handle_upload(stream: FakeClientStream, headers: dict[str, str]) -> Response:
    """Shape of an HTTP handler using the bounded reader."""
    try:
        body = read_body(stream, headers)
    except BodyRejected as rejection:
        return Response(rejection.status, rejection.reason)
    return Response(HTTP_OK, f"received {len(body)} bytes")
