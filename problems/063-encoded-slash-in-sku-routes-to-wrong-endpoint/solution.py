"""Route URLs whose path parameters contain an encoded slash ("%2F") correctly.

About 4% of a marketplace's SKUs come from a supplier that uses "/" inside
the SKU (``TOOLS/DRILL-18V``). Clients correctly sent it percent-encoded as
``/v1/products/TOOLS%2FDRILL-18V``, but the router decoded the whole path
first and only then split it on "/", so the SKU became two path segments.
Those requests hit the wrong route (or none) and returned 404.

The fix splits the raw path on "/" first and decodes each segment on its own,
so an encoded slash stays part of its segment. Invalid percent-escapes are
rejected instead of being passed through, and each segment is decoded exactly
once, so a double-encoded "%252F" is not turned into a slash either.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import unquote

# Templates are written with "{name}" placeholders for path parameters.
ROUTES: list[tuple[str, str]] = [
    ("get_product", "/v1/products/{sku}"),
    ("get_product_stock_in_store", "/v1/products/{sku}/{store_id}"),
]

HEX_DIGITS = "0123456789abcdefABCDEF"


class BadPathError(ValueError):
    """The request path is not valid percent-encoded UTF-8 (answer 400)."""


@dataclass
class RouteMatch:
    """Which handler a request goes to, and its decoded path parameters."""
    handler: str
    params: dict[str, str] = field(default_factory=dict)


def template_segments(template: str) -> list[str]:
    """Split a route template like "/v1/products/{sku}" into its segments."""
    return template.strip("/").split("/")


def match_segments(template: list[str], segments: list[str]) -> dict[str, str] | None:
    """Return the path parameters if the segments fit the template, else None."""
    if len(template) != len(segments):
        return None
    params: dict[str, str] = {}
    for template_part, segment in zip(template, segments):
        if template_part.startswith("{") and template_part.endswith("}"):
            params[template_part[1:-1]] = segment
        elif template_part != segment:
            return None
    return params


def find_route(segments: list[str]) -> RouteMatch | None:
    """First route whose template matches the already-decoded segments."""
    for handler, template in ROUTES:
        params = match_segments(template_segments(template), segments)
        if params is not None:
            return RouteMatch(handler, params)
    return None


def naive_route(raw_path: str) -> RouteMatch | None:
    """The original router: decode the whole path, then split on "/"."""
    decoded_path = unquote(raw_path)
    return find_route(decoded_path.strip("/").split("/"))


def check_percent_escapes(segment: str) -> None:
    """Reject "%" not followed by two hex digits.

    ``unquote`` silently leaves such text as it is, so "100%" and "100%25"
    would both reach the handler as "100%" and look like the same SKU.
    """
    index = segment.find("%")
    while index != -1:
        escape = segment[index + 1:index + 3]
        if len(escape) != 2 or escape[0] not in HEX_DIGITS or escape[1] not in HEX_DIGITS:
            raise BadPathError(f"invalid percent-escape in path segment {segment!r}")
        index = segment.find("%", index + 3)


def decode_segment(segment: str) -> str:
    """Decode one raw path segment exactly once, as strict UTF-8."""
    check_percent_escapes(segment)
    try:
        return unquote(segment, encoding="utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise BadPathError(f"path segment {segment!r} is not valid UTF-8") from error


def route(raw_path: str) -> RouteMatch | None:
    """Split the raw path on "/" first, then decode each segment on its own.

    Only a literal "/" in the raw path separates segments; "%2F" is data
    inside a segment, which is exactly what the client meant by encoding it.
    """
    raw_segments = raw_path.strip("/").split("/")
    decoded_segments = [decode_segment(segment) for segment in raw_segments]
    return find_route(decoded_segments)
