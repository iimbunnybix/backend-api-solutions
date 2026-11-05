"""PATCH /profile wiped fields the client never sent, and could not clear a field.

The original handler read every field with ``body.get(name)``, so a field
missing from the JSON body became ``None`` and overwrote the stored value.
The first "fix" skipped every ``None``, which made it impossible to clear a
field on purpose. This module treats the three cases separately, like JSON
Merge Patch (RFC 7396): absent = keep, ``null`` = clear (only for nullable
fields), value = set after type validation. Unknown fields are rejected.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class PatchValidationError(ValueError):
    """The PATCH body is invalid (map to HTTP 422 with the message)."""


class _Missing:
    """Sentinel type meaning "the client did not send this field"."""

    def __repr__(self) -> str:
        return "MISSING"


# A unique object, so it can never be confused with None or any JSON value.
MISSING = _Missing()


@dataclass(frozen=True)
class FieldRule:
    python_type: type
    nullable: bool
    max_length: int = 200


PROFILE_FIELDS: dict[str, FieldRule] = {
    "display_name": FieldRule(str, nullable=False, max_length=50),
    "phone": FieldRule(str, nullable=True, max_length=20),
    "bio": FieldRule(str, nullable=True, max_length=500),
}


def original_patch_profile(stored: dict[str, Any], body: dict[str, Any]) -> dict[str, Any]:
    """The original bug: a missing field reads as None and erases stored data."""
    updated = dict(stored)
    for name in PROFILE_FIELDS:
        updated[name] = body.get(name)
    return updated


def skip_none_patch_profile(stored: dict[str, Any], body: dict[str, Any]) -> dict[str, Any]:
    """The first hotfix: ignore None, so an explicit null can never clear a field."""
    updated = dict(stored)
    for name in PROFILE_FIELDS:
        value = body.get(name)
        if value is not None:
            updated[name] = value
    return updated


def _validate_value(name: str, rule: FieldRule, value: Any) -> None:
    """Check one sent value against its rule; raise PatchValidationError if bad."""
    if value is None:
        if not rule.nullable:
            raise PatchValidationError(f"{name} cannot be null")
        return
    # bool is a subclass of int in Python, so compare exact types.
    if type(value) is not rule.python_type:
        raise PatchValidationError(f"{name} must be a {rule.python_type.__name__}")
    if isinstance(value, str) and len(value) > rule.max_length:
        raise PatchValidationError(f"{name} is longer than {rule.max_length} characters")


def parse_patch(body: Any) -> dict[str, Any]:
    """Turn a decoded JSON body into {field: value or None} for sent fields only.

    Fields not in the body are left out entirely, so the caller can tell
    "not sent" apart from "sent as null".
    """
    if not isinstance(body, dict):
        raise PatchValidationError("PATCH body must be a JSON object")
    unknown = sorted(set(body) - set(PROFILE_FIELDS))
    # Rejecting typos like "phnoe" beats silently ignoring the client's intent.
    if unknown:
        raise PatchValidationError(f"unknown fields: {', '.join(unknown)}")
    changes: dict[str, Any] = {}
    for name, rule in PROFILE_FIELDS.items():
        value = body.get(name, MISSING)
        if value is MISSING:
            continue
        _validate_value(name, rule, value)
        changes[name] = value
    return changes


def apply_patch(stored: dict[str, Any], body: Any) -> dict[str, Any]:
    """Return a new profile with the PATCH applied; ``stored`` is not modified.

    The whole body is validated before anything is applied, so a bad field
    never leaves a half-updated profile.
    """
    changes = parse_patch(body)
    updated = dict(stored)
    for name, value in changes.items():
        updated[name] = value
    return updated
