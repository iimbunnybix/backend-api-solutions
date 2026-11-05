"""Appointment API that sends times with an explicit UTC offset.

The original booking API stored and returned naive datetimes such as
"2026-03-10T09:00:00" (meant as UTC, but with no offset written down). Browsers
and phones parse a timestamp without an offset as *their own local time*, so a
patient in India saw a 09:00 UTC slot as 09:00 IST, 5.5 hours early, and staff
in New York saw the same slot at 09:00 their time. This version refuses naive
datetimes at the API boundary, converts everything to UTC for storage, and
always emits ISO-8601 with the offset ("2026-03-10T09:00:00+00:00").
"""
from __future__ import annotations

import json
from datetime import datetime, timezone, tzinfo

# Some clients send "Z" for UTC; datetime.fromisoformat only accepts it from
# Python 3.11, so it is translated to "+00:00" first to support 3.10.
UTC_DESIGNATOR = "Z"
UTC_OFFSET_TEXT = "+00:00"


class NaiveDatetimeError(ValueError):
    """Raised when a datetime has no timezone, so its instant is unknown (HTTP 400)."""


def naive_serialize_appointment(appointment_id: int, starts_at: datetime) -> str:
    """Original encoder: writes whatever datetime it gets, offset or not."""
    return json.dumps({"id": appointment_id, "starts_at": starts_at.isoformat()})


def parse_like_client(timestamp_text: str, client_timezone: tzinfo) -> datetime:
    """Parse a timestamp the way a browser or phone does, in the client's zone.

    A timestamp with an offset is an exact instant. One without an offset is
    read as local time on the device: this is the step that shifts the hour.
    """
    parsed = datetime.fromisoformat(timestamp_text.replace(UTC_DESIGNATOR, UTC_OFFSET_TEXT))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=client_timezone)
    return parsed.astimezone(client_timezone)


def require_aware(value: datetime) -> datetime:
    """Reject naive datetimes instead of guessing which zone they were meant in."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise NaiveDatetimeError(f"datetime has no timezone: {value.isoformat()}")
    return value


def to_utc(value: datetime) -> datetime:
    """Convert an aware datetime to UTC, the only zone the database stores."""
    return require_aware(value).astimezone(timezone.utc)


def parse_api_timestamp(timestamp_text: str) -> datetime:
    """Parse a timestamp from a request body; it must carry an offset.

    Returns the instant in UTC, ready to store.
    """
    try:
        parsed = datetime.fromisoformat(timestamp_text.replace(UTC_DESIGNATOR, UTC_OFFSET_TEXT))
    except ValueError as error:
        raise NaiveDatetimeError(f"not an ISO-8601 timestamp: {timestamp_text!r}") from error
    return to_utc(parsed)


def format_api_timestamp(value: datetime) -> str:
    """Format an instant for responses: always UTC, always with "+00:00"."""
    return to_utc(value).isoformat()


def serialize_appointment(appointment_id: int, starts_at: datetime) -> str:
    """Fixed encoder: the timestamp always says which instant it is."""
    return json.dumps({"id": appointment_id, "starts_at": format_api_timestamp(starts_at)})
