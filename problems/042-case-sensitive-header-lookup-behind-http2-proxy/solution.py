"""HTTP headers looked up case-sensitively in a plain dict.

The payment API built ``dict(raw_headers)`` and read ``headers["Content-Type"]``
and ``headers.get("Idempotency-Key")``. HTTP header names are
case-insensitive, and HTTP/2 requires them in lowercase, so once an HTTP/2
proxy was put in front of the service those lookups missed: JSON requests got
415, and retries lost their idempotency key and charged twice. The dict also
kept only the last of repeated header lines.

The fix is a ``Headers`` multi-map: lookups ignore case, every field line is
kept in order, single-value headers reject conflicting duplicates, and
list-valued headers can be read as one combined list.
"""
from __future__ import annotations

STATUS_OK = 200
STATUS_BAD_REQUEST = 400
STATUS_UNSUPPORTED_MEDIA_TYPE = 415
JSON_MEDIA_TYPE = "application/json"

RawHeaders = list[tuple[str, str]]
Response = tuple[int, str]


class DuplicateHeader(ValueError):
    """A header that allows one value was sent with different values."""


class Headers:
    """Case-insensitive, order-preserving multi-map of HTTP header fields."""

    def __init__(self, raw: RawHeaders | None = None) -> None:
        # name.lower() -> every value in arrival order; the original spelling
        # is kept separately so headers can be forwarded as received.
        self._values: dict[str, list[str]] = {}
        self._lines: RawHeaders = []
        for name, value in raw or []:
            self.add(name, value)

    def add(self, name: str, value: str) -> None:
        """Append one field line; earlier lines with the same name are kept."""
        self._lines.append((name, value))
        self._values.setdefault(name.lower(), []).append(value.strip())

    def __contains__(self, name: object) -> bool:
        """``"content-type" in headers`` ignores case."""
        return isinstance(name, str) and name.lower() in self._values

    def get_all(self, name: str) -> list[str]:
        """Every value sent for ``name``, one per field line (Set-Cookie)."""
        return list(self._values.get(name.lower(), []))

    def get_list(self, name: str) -> list[str]:
        """Comma-separated list header across all its lines (Accept, Via)."""
        items = []
        for value in self.get_all(name):
            for item in value.split(","):
                if item.strip():
                    items.append(item.strip())
        return items

    def get_one(self, name: str) -> str | None:
        """The single value of ``name``, or None if absent.

        Raises DuplicateHeader if lines disagree: picking either one would let
        a client (or a proxy in between) choose what the service sees.
        """
        values = self.get_all(name)
        if not values:
            return None
        if len(set(values)) > 1:
            raise DuplicateHeader(f"conflicting values for {name}: {values}")
        return values[0]

    def lines(self) -> RawHeaders:
        """All field lines exactly as received, for logging or forwarding."""
        return list(self._lines)


def media_type(content_type: str | None) -> str | None:
    """``"Application/JSON; charset=utf-8"`` -> ``"application/json"``."""
    if content_type is None:
        return None
    return content_type.split(";", 1)[0].strip().lower()


class PaymentService:
    """Charges a card; retries with the same idempotency key charge once."""

    def __init__(self) -> None:
        self.charges: list[str] = []
        self.responses_by_key: dict[str, Response] = {}

    def charge(self, body: str) -> Response:
        """Record one real charge."""
        self.charges.append(body)
        return STATUS_OK, f"charged #{len(self.charges)}"


def naive_handle_payment(service: PaymentService, raw: RawHeaders, body: str) -> Response:
    """The original handler: plain dict, exact-case keys."""
    headers = dict(raw)
    if headers.get("Content-Type") != JSON_MEDIA_TYPE:
        return STATUS_UNSUPPORTED_MEDIA_TYPE, "expected application/json"
    key = headers.get("Idempotency-Key")
    if key is None:
        # Keys were "optional", so a missed lookup charged without dedupe.
        return service.charge(body)
    if key not in service.responses_by_key:
        service.responses_by_key[key] = service.charge(body)
    return service.responses_by_key[key]


def handle_payment(service: PaymentService, raw: RawHeaders, body: str) -> Response:
    """Fixed handler: case-insensitive lookups, strict single values."""
    headers = Headers(raw)
    try:
        content_type = headers.get_one("Content-Type")
        key = headers.get_one("Idempotency-Key")
    except DuplicateHeader as error:
        return STATUS_BAD_REQUEST, str(error)
    if media_type(content_type) != JSON_MEDIA_TYPE:
        return STATUS_UNSUPPORTED_MEDIA_TYPE, "expected application/json"
    if not key:
        # Fail loudly: a missing key must never turn into a silent double charge.
        return STATUS_BAD_REQUEST, "Idempotency-Key header is required"
    if key not in service.responses_by_key:
        service.responses_by_key[key] = service.charge(body)
    return service.responses_by_key[key]
