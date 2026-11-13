"""Build HTTP responses whose Content-Length counts bytes, not characters.

A hand-rolled response writer set `Content-Length: len(body)` on a Python
`str`. For ASCII that equals the byte count, but "Zoë" or "₹499" takes more
bytes than characters in UTF-8. Clients read exactly Content-Length bytes, so
JSON with non-ASCII text arrived truncated and failed to parse, and on a
keep-alive connection the leftover bytes were read as the start of the next
response. This writer encodes the body first and measures the bytes.
"""
from __future__ import annotations

from dataclasses import dataclass

HEADER_SEPARATOR = b"\r\n\r\n"
BODY_ENCODING = "utf-8"
STATUS_LINE_PREFIX = "HTTP/1.1 "


def naive_build_response(body: str) -> bytes:
    """Original writer: Content-Length is the number of *characters*."""
    head = f"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {len(body)}\r\n\r\n"
    return head.encode("ascii") + body.encode(BODY_ENCODING)


def build_response(body: str) -> bytes:
    """Fixed writer: encode once, then advertise the length of those exact bytes.

    The charset is stated explicitly so the client decodes with the same
    encoding that was used to count.
    """
    body_bytes = body.encode(BODY_ENCODING)
    head = (
        "HTTP/1.1 200 OK\r\n"
        f"Content-Type: application/json; charset={BODY_ENCODING}\r\n"
        f"Content-Length: {len(body_bytes)}\r\n\r\n"
    )
    return head.encode("ascii") + body_bytes


@dataclass
class ParsedResponse:
    headers: dict[str, str]
    body: bytes


def _parse_headers(head: bytes) -> dict[str, str]:
    """Check the status line, then turn the header lines into a lower-cased dict."""
    headers: dict[str, str] = {}
    lines = head.decode("ascii", errors="replace").split("\r\n")
    # A desynchronised connection shows up here: the "status line" starts
    # with leftover body bytes instead of the protocol version.
    if not lines[0].startswith(STATUS_LINE_PREFIX):
        raise ValueError(f"bad status line: {lines[0]!r}")
    for line in lines[1:]:
        name, _, value = line.partition(":")
        headers[name.strip().lower()] = value.strip()
    return headers


def read_one_response(stream: bytes) -> tuple[ParsedResponse, bytes]:
    """Read one response and return it with whatever bytes follow it.

    Like a real client, it trusts Content-Length to know where the body ends.
    """
    head, separator, rest = stream.partition(HEADER_SEPARATOR)
    if not separator:
        raise ValueError(f"garbage on connection: {stream[:20]!r}")
    headers = _parse_headers(head)
    length = int(headers["content-length"])
    return ParsedResponse(headers, rest[:length]), rest[length:]


def read_responses(stream: bytes) -> list[ParsedResponse]:
    """Read back-to-back responses from one keep-alive connection.

    Bytes left over from a wrong Content-Length are taken as the start of the
    next response, which is how one bad length corrupts later responses.
    """
    responses = []
    remaining = stream
    while remaining:
        response, remaining = read_one_response(remaining)
        responses.append(response)
    return responses
