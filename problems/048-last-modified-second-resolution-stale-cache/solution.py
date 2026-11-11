"""Conditional GET that cannot miss an update made within the same second.

The product API answered ``If-Modified-Since`` by comparing timestamps. HTTP
dates only have one-second resolution, so a change made later in the same
second as a client's fetch looked "not modified" and the client kept the old
price indefinitely. The fix validates with a strong ETag built from the row
version (which takes precedence over If-Modified-Since, as RFC 9110 requires)
and only sends Last-Modified once that second is over, so the date can never
stand for two different versions.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from email.utils import formatdate, parsedate_to_datetime

HTTP_OK = 200
HTTP_NOT_MODIFIED = 304


class FakeClock:
    """Controllable wall clock in fractional epoch seconds."""

    def __init__(self, now: float):
        self.now = now


@dataclass
class Product:
    price_cents: int
    version: int
    modified_at: float


@dataclass
class Response:
    status: int
    headers: dict[str, str]
    price_cents: int | None = None


class ProductStore:
    """In-memory product table; every update bumps the version."""

    def __init__(self, clock: FakeClock):
        self.clock = clock
        self.products: dict[str, Product] = {}

    def set_price(self, product_id: str, price_cents: int) -> None:
        """Save a new price, recording a new version and the exact time."""
        previous = self.products.get(product_id)
        version = previous.version + 1 if previous else 1
        self.products[product_id] = Product(price_cents, version, self.clock.now)


def http_date(epoch_seconds: float) -> str:
    """Format as an HTTP date; this drops everything below one second."""
    return formatdate(int(epoch_seconds), usegmt=True)


def parse_http_date(value: str) -> int:
    """Parse an HTTP date into whole epoch seconds."""
    return int(parsedate_to_datetime(value).timestamp())


def naive_get(store: ProductStore, product_id: str, request_headers: dict[str, str]) -> Response:
    """Original handler: Last-Modified always sent, If-Modified-Since compared by second."""
    product = store.products[product_id]
    headers = {"Last-Modified": http_date(product.modified_at)}
    since = request_headers.get("If-Modified-Since")
    # Bug: an update later in the same second truncates to the same date.
    if since is not None and int(product.modified_at) <= parse_http_date(since):
        return Response(HTTP_NOT_MODIFIED, headers)
    return Response(HTTP_OK, headers, product.price_cents)


def strong_etag(product: Product) -> str:
    """Validator that changes on every update, however close together."""
    return f'"v{product.version}"'


def last_modified_is_safe(product: Product, now: float) -> bool:
    """True once the modification second is over.

    While we are still inside that second another update could land with the
    same HTTP date, so the date is not yet a reliable validator.
    """
    return int(product.modified_at) < int(now)


def response_headers(product: Product, now: float) -> dict[str, str]:
    """Validators for a response: always the ETag, Last-Modified only when safe."""
    headers = {"ETag": strong_etag(product)}
    if last_modified_is_safe(product, now):
        headers["Last-Modified"] = http_date(product.modified_at)
    return headers


def is_not_modified(product: Product, now: float, request_headers: dict[str, str]) -> bool:
    """Evaluate the client's validators the way RFC 9110 section 13.2.2 orders them."""
    if_none_match = request_headers.get("If-None-Match")
    # If-None-Match wins; If-Modified-Since is ignored when it is present.
    if if_none_match is not None:
        return strong_etag(product) in parse_etag_list(if_none_match)
    since = request_headers.get("If-Modified-Since")
    if since is None or not last_modified_is_safe(product, now):
        return False
    return int(product.modified_at) <= parse_http_date(since)


def parse_etag_list(header_value: str) -> list[str]:
    """Split an If-None-Match header such as '"v1", "v2"' into tags."""
    tags = []
    for part in header_value.split(","):
        tags.append(part.strip())
    return tags


def fixed_get(store: ProductStore, product_id: str, request_headers: dict[str, str]) -> Response:
    """Fixed handler: version ETag first, Last-Modified only after its second ends."""
    product = store.products[product_id]
    headers = response_headers(product, store.clock.now)
    if is_not_modified(product, store.clock.now, request_headers):
        return Response(HTTP_NOT_MODIFIED, headers)
    return Response(HTTP_OK, headers, product.price_cents)


@dataclass
class CachingClient:
    """Mobile app cache: stores the body and revalidates with what it was given."""

    cached_price: int | None = None
    validators: dict[str, str] = field(default_factory=dict)

    def fetch(self, handler, store: ProductStore, product_id: str) -> int | None:
        """Revalidate and return the price the user would see."""
        request_headers = {}
        if "ETag" in self.validators:
            request_headers["If-None-Match"] = self.validators["ETag"]
        if "Last-Modified" in self.validators:
            request_headers["If-Modified-Since"] = self.validators["Last-Modified"]
        response = handler(store, product_id, request_headers)
        if response.status == HTTP_OK:
            self.cached_price = response.price_cents
            self.validators = response.headers
        return self.cached_price


def same_second_correction(handler) -> int | None:
    """Price typo fixed 0.5 s after it was saved; the app fetched in between."""
    clock = FakeClock(now=1_762_862_400.2)
    store = ProductStore(clock)
    client = CachingClient()
    store.set_price("sku-1", 19_990)        # typo saved at .2
    clock.now += 0.2
    client.fetch(handler, store, "sku-1")   # app fetches at .4
    clock.now += 0.3
    store.set_price("sku-1", 1_999)         # correction at .7, same second
    clock.now += 60
    return client.fetch(handler, store, "sku-1")
