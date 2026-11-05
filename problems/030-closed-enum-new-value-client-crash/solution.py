"""Order-status client that keeps working when the server adds a new enum value.

The order-tracking client parsed each order's ``status`` with a closed enum,
``OrderStatus(raw)``. When the backend shipped click-and-collect and started
returning ``"READY_FOR_PICKUP"``, that call raised ``ValueError``, and because the
whole order list was parsed in one go, a single new-status order made the
customer's entire order history fail to load.

The fix is a *tolerant reader*: known values map to their enum member, any
other string maps to ``UNKNOWN`` while keeping the raw text, unknown values are
counted for alerting, and business rules treat ``UNKNOWN`` conservatively.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional

STATUS_FIELD = "status"
UNKNOWN_STATUS_LABEL = "In progress"


class OrderStatus(Enum):
    """The statuses this client version was built with, plus a catch-all."""

    PLACED = "PLACED"
    PAID = "PAID"
    SHIPPED = "SHIPPED"
    DELIVERED = "DELIVERED"
    CANCELLED = "CANCELLED"
    UNKNOWN = "UNKNOWN"  # never sent by the server; means "newer than this client"


STATUS_LABELS = {
    OrderStatus.PLACED: "Order placed",
    OrderStatus.PAID: "Payment received",
    OrderStatus.SHIPPED: "On the way",
    OrderStatus.DELIVERED: "Delivered",
    OrderStatus.CANCELLED: "Cancelled",
}
CANCELLABLE_STATUSES = (OrderStatus.PLACED, OrderStatus.PAID)


@dataclass(frozen=True)
class Order:
    order_id: str
    status: OrderStatus
    raw_status: str  # what the server actually sent, kept for logs and support


# --- the original, buggy client ----------------------------------------------
def naive_parse_order(payload: dict[str, Any]) -> Order:
    """Closed-enum parse: any status this build does not know raises ValueError."""
    raw = payload[STATUS_FIELD]
    return Order(payload["id"], OrderStatus(raw), raw)


def naive_parse_order_list(payloads: list[dict[str, Any]]) -> list[Order]:
    """One unexpected status anywhere fails the whole list."""
    return [naive_parse_order(payload) for payload in payloads]


# --- the fix ------------------------------------------------------------------
class UnknownValueCounter:
    """Counts unknown enum values so a dashboard can show "clients are out of date"."""

    def __init__(self) -> None:
        self.counts: dict[tuple[str, str], int] = {}

    def record(self, field: str, raw_value: str) -> None:
        key = (field, raw_value)
        self.counts[key] = self.counts.get(key, 0) + 1


def parse_status(raw: Any, counter: Optional[UnknownValueCounter] = None) -> OrderStatus:
    """Map a known status string to its member and any other string to UNKNOWN.

    Non-strings are still rejected: a number or null is a broken payload, not a
    newer server, and hiding it would mask real bugs.
    """
    if not isinstance(raw, str):
        raise ValueError(f"status must be a string, got {raw!r}")
    # WHY exclude UNKNOWN: it is our own placeholder, not a value the server may claim.
    for member in OrderStatus:
        if member is not OrderStatus.UNKNOWN and member.value == raw:
            return member
    if counter is not None:
        counter.record(STATUS_FIELD, raw)
    return OrderStatus.UNKNOWN


def parse_order(payload: dict[str, Any], counter: Optional[UnknownValueCounter] = None) -> Order:
    """Parse one order, tolerating status values added after this client shipped."""
    raw = payload[STATUS_FIELD]
    return Order(payload["id"], parse_status(raw, counter), raw)


def parse_order_list(payloads: list[dict[str, Any]],
                     counter: Optional[UnknownValueCounter] = None) -> list[Order]:
    """Parse every order; a new status value never fails the list."""
    orders = []
    for payload in payloads:
        orders.append(parse_order(payload, counter))
    return orders


def status_label(order: Order) -> str:
    """Text shown in the app; unknown statuses get a neutral label, not a crash."""
    if order.status is OrderStatus.UNKNOWN:
        return UNKNOWN_STATUS_LABEL
    return STATUS_LABELS[order.status]


def can_cancel(order: Order) -> bool:
    """Only offer "Cancel" for statuses we know are cancellable.

    WHY an allow-list: a new status might mean the parcel is already packed, so
    UNKNOWN must fall on the safe side (no cancel button).
    """
    return order.status in CANCELLABLE_STATUSES
