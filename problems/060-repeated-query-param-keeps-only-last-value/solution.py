"""Parse repeated query parameters as lists instead of keeping only the last one.

An order-search endpoint accepts filters like ?status=pending&status=failed.
The handler parsed the query string with dict(parse_qsl(query)), which keeps
only the LAST value of a repeated key, so the dashboard asking for "pending or
failed" orders silently got only failed ones. This module parses with a small
schema: list parameters keep every value, single parameters that appear twice
are rejected with a 400 instead of one copy being picked silently.
"""
from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import parse_qs, parse_qsl

ALLOWED_STATUSES = frozenset({"pending", "paid", "failed", "refunded"})
DEFAULT_LIMIT = 20
MAX_LIMIT = 100


class BadRequest(ValueError):
    """Maps to HTTP 400: the client sent a query we refuse to guess about."""


@dataclass(frozen=True)
class OrderFilter:
    statuses: frozenset[str]   # empty means "any status"
    customer_id: str | None
    limit: int


def naive_parse_filters(query: str) -> dict[str, str]:
    """Original code: a dict keeps one value per key, the last one wins."""
    return dict(parse_qsl(query))


def naive_search(orders: list[dict[str, str]], query: str) -> list[dict[str, str]]:
    """Original handler built on naive_parse_filters."""
    params = naive_parse_filters(query)
    wanted_status = params.get("status")
    results = []
    for order in orders:
        if wanted_status is None or order["status"] == wanted_status:
            results.append(order)
    return results


def single_value(params: dict[str, list[str]], name: str) -> str | None:
    """Return the one value of a single-valued parameter, or None if absent.

    Two copies of e.g. customer_id are ambiguous (different proxies and
    frameworks pick the first or the last), so the request is refused.
    """
    values = params.get(name)
    if values is None:
        return None
    if len(values) > 1:
        raise BadRequest(f"parameter {name!r} given {len(values)} times")
    return values[0]


def parse_statuses(params: dict[str, list[str]]) -> frozenset[str]:
    """Collect every status value, so status=a&status=b means a OR b."""
    statuses = set()
    for value in params.get("status", []):
        if value not in ALLOWED_STATUSES:
            raise BadRequest(f"unknown status {value!r}")
        statuses.add(value)
    return frozenset(statuses)


def parse_limit(raw_limit: str | None) -> int:
    """Validate the page size; only ASCII digits are accepted."""
    if raw_limit is None:
        return DEFAULT_LIMIT
    if not (raw_limit.isascii() and raw_limit.isdigit()):
        raise BadRequest("limit must be a positive integer")
    limit = int(raw_limit)
    if limit < 1 or limit > MAX_LIMIT:
        raise BadRequest(f"limit must be between 1 and {MAX_LIMIT}")
    return limit


def parse_filters(query: str) -> OrderFilter:
    """Parse the order-search query string according to the endpoint's schema."""
    # parse_qs keeps every value of a repeated key, in order, as a list.
    # keep_blank_values makes "status=" an explicit (invalid) value, not a no-op.
    params = parse_qs(query, keep_blank_values=True)
    known = {"status", "customer_id", "limit"}
    unknown = sorted(set(params) - known)
    if unknown:
        # A typo such as "stauts" would otherwise silently return every order.
        raise BadRequest(f"unknown parameters: {', '.join(unknown)}")
    return OrderFilter(
        statuses=parse_statuses(params),
        customer_id=single_value(params, "customer_id"),
        limit=parse_limit(single_value(params, "limit")),
    )


def search(orders: list[dict[str, str]], query: str) -> list[dict[str, str]]:
    """Return orders matching every filter, up to the page limit."""
    order_filter = parse_filters(query)
    results = []
    for order in orders:
        if order_filter.statuses and order["status"] not in order_filter.statuses:
            continue
        if order_filter.customer_id is not None and order["customer_id"] != order_filter.customer_id:
            continue
        results.append(order)
        if len(results) == order_filter.limit:
            break
    return results
