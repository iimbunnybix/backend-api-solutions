"""JSON API that rejected valid requests because Content-Type had a charset.

The orders endpoint only accepted a body if its Content-Type header was the
exact string "application/json". A new app release switched HTTP libraries,
which sends "application/json; charset=utf-8", so every order from the new
version got 415 Unsupported Media Type, though the body was perfectly fine.

The fix parses the header as a media type: the type/subtype is compared
case-insensitively, parameters after ";" are parsed rather than compared as
text, "+json" types are accepted, and a charset other than UTF-8 is rejected
with a clear message, since JSON over HTTP must be UTF-8 (RFC 8259).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

JSON_MEDIA_TYPE = "application/json"
JSON_SUFFIX = "+json"  # structured syntax suffix, e.g. application/merge-patch+json
ALLOWED_CHARSETS = ("utf-8", "utf8")
STATUS_OK = 200
STATUS_BAD_REQUEST = 400
STATUS_UNSUPPORTED_MEDIA_TYPE = 415


@dataclass(frozen=True)
class MediaType:
    """A parsed Content-Type value: "type/subtype" plus its parameters."""

    essence: str  # lower-case "type/subtype"
    parameters: dict[str, str] = field(default_factory=dict)  # names lower-cased


# --- the original, buggy version ---------------------------------------------
def naive_is_json(content_type: str | None) -> bool:
    """Original check: exact string match, so any parameter or capital fails."""
    return content_type == JSON_MEDIA_TYPE


# --- the fix ------------------------------------------------------------------
def parse_media_type(header_value: str) -> MediaType | None:
    """Split "type/subtype; name=value; ..." into its parts, or None if malformed."""
    pieces = header_value.split(";")
    essence = pieces[0].strip().lower()
    # WHY: a media type must be exactly one "type/subtype" with both parts present.
    type_and_subtype = essence.split("/")
    if len(type_and_subtype) != 2 or not type_and_subtype[0] or not type_and_subtype[1]:
        return None
    parameters = {}
    for piece in pieces[1:]:
        name, separator, value = piece.partition("=")
        if not separator:
            continue  # tolerate a stray ";" or a parameter without a value
        parameters[name.strip().lower()] = unquote(value.strip())
    return MediaType(essence, parameters)


def unquote(value: str) -> str:
    """Parameter values may be quoted strings: charset="utf-8" equals charset=utf-8."""
    if len(value) >= 2 and value.startswith('"') and value.endswith('"'):
        return value[1:-1]
    return value


def is_json_essence(essence: str) -> bool:
    """application/json, or any application/...+json type such as merge-patch+json."""
    if essence == JSON_MEDIA_TYPE:
        return True
    return essence.startswith("application/") and essence.endswith(JSON_SUFFIX)


def check_json_content_type(content_type: str | None) -> str | None:
    """Return None if the body may be read as JSON, else the reason it may not."""
    if content_type is None:
        return "missing Content-Type"
    media_type = parse_media_type(content_type)
    if media_type is None:
        return "malformed Content-Type"
    if not is_json_essence(media_type.essence):
        return f"expected application/json, got {media_type.essence}"
    charset = media_type.parameters.get("charset")
    # WHY reject other charsets: the body would be decoded wrongly, not just labelled wrongly.
    if charset is not None and charset.lower() not in ALLOWED_CHARSETS:
        return f"JSON must be UTF-8, got charset={charset}"
    return None


def handle_create_order(content_type: str | None, body: bytes) -> tuple[int, dict]:
    """POST /orders: validate the media type, then parse the body as UTF-8 JSON."""
    problem = check_json_content_type(content_type)
    if problem is not None:
        return STATUS_UNSUPPORTED_MEDIA_TYPE, {"error": problem}
    try:
        order = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return STATUS_BAD_REQUEST, {"error": "body is not valid UTF-8 JSON"}
    return STATUS_OK, {"accepted": order.get("order_id")}


def naive_handle_create_order(content_type: str | None, body: bytes) -> tuple[int, dict]:
    """The original handler, kept for comparison."""
    if not naive_is_json(content_type):
        return STATUS_UNSUPPORTED_MEDIA_TYPE, {"error": "Content-Type must be application/json"}
    order = json.loads(body.decode("utf-8"))
    return STATUS_OK, {"accepted": order.get("order_id")}
