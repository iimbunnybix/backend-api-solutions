"""Split an order-level discount across line items so the shares add up exactly.

A checkout service applied a flat coupon (for example 100 rupees off) to a whole
order, then spread it across the line items in proportion to their price so
invoices and partial refunds could show a per-item discount. Each share was
rounded to the paisa on its own, so the shares added up to a few paise more or
less than the coupon. This module uses the largest-remainder method on integer
paise: round every share down, then hand the leftover paise one at a time to
the items with the biggest dropped fractions, so the total is always exact.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LineItem:
    sku: str
    price_paise: int  # integer paise: never store money as float


def naive_split_discount(discount_paise: int, items: list[LineItem]) -> dict[str, int]:
    """Original code: round each proportional share independently."""
    order_total = sum(item.price_paise for item in items)
    shares: dict[str, int] = {}
    for item in items:
        shares[item.sku] = round(discount_paise * item.price_paise / order_total)
    return shares


def validate_split_inputs(discount_paise: int, items: list[LineItem]) -> int:
    """Reject inputs that have no sensible split and return the order total."""
    if not items:
        raise ValueError("cannot split a discount across an empty order")
    if len({item.sku for item in items}) != len(items):
        raise ValueError("duplicate sku in order")
    for item in items:
        if item.price_paise < 0:
            raise ValueError(f"{item.sku}: negative price")
    order_total = sum(item.price_paise for item in items)
    if order_total == 0:
        raise ValueError("order total is zero")
    if discount_paise < 0 or discount_paise > order_total:
        raise ValueError("discount must be between 0 and the order total")
    return order_total


def split_discount(discount_paise: int, items: list[LineItem]) -> dict[str, int]:
    """Split discount_paise in proportion to price; shares always sum to the discount.

    Uses integer arithmetic only, so there is no float rounding at any step.
    """
    order_total = validate_split_inputs(discount_paise, items)
    shares: dict[str, int] = {}
    remainders: list[tuple[int, int, str]] = []
    for position, item in enumerate(items):
        # divmod gives the rounded-down share and the exact dropped fraction
        # (as a numerator over order_total), with no float involved.
        floor_share, remainder = divmod(discount_paise * item.price_paise, order_total)
        shares[item.sku] = floor_share
        # Sort key: biggest remainder first; ties go to the earlier line so the
        # result is the same every time the invoice is regenerated.
        remainders.append((-remainder, position, item.sku))
    leftover_paise = discount_paise - sum(shares.values())
    remainders.sort()
    # leftover_paise is always smaller than the number of items, because each
    # item dropped strictly less than one paisa.
    for index in range(leftover_paise):
        sku = remainders[index][2]
        shares[sku] += 1
    return shares


def refund_for_items(skus: list[str], items: list[LineItem],
                     shares: dict[str, int]) -> int:
    """Amount to refund when the customer returns these items: price minus its discount."""
    prices = {item.sku: item.price_paise for item in items}
    refund = 0
    for sku in skus:
        refund += prices[sku] - shares[sku]
    return refund
