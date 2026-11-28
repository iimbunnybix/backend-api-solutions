"""Stop one customer's warnings from appearing in other customers' responses.

The quote endpoint collected validation warnings in a list that was a default
argument: ``def build_quote(items, warnings=[])``. Python evaluates a default
once, when the function is defined, so every request that did not pass its own
list appended to the same shared list. Warnings (with other customers' SKUs)
piled up in every later response until the worker restarted.

Fix: use ``None`` as the default and create a fresh list inside the call, and
return the warnings as part of the result instead of mutating a caller's list.
"""
from __future__ import annotations

import inspect
from dataclasses import dataclass, field

MAX_QUANTITY_PER_LINE = 100      # business rule: larger orders go to sales


@dataclass(frozen=True)
class LineItem:
    sku: str
    unit_price_cents: int
    quantity: int


@dataclass
class Quote:
    total_cents: int
    warnings: list[str] = field(default_factory=list)


def check_line(item: LineItem) -> str | None:
    """Return a warning for one line, or None when the line is fine."""
    if item.quantity > MAX_QUANTITY_PER_LINE:
        return f"{item.sku}: quantity {item.quantity} capped at {MAX_QUANTITY_PER_LINE}"
    if item.unit_price_cents == 0:
        return f"{item.sku}: free item, check the price list"
    return None


def billable_quantity(item: LineItem) -> int:
    """Quantity actually priced: anything over the cap is quoted by sales."""
    return min(item.quantity, MAX_QUANTITY_PER_LINE)


def original_build_quote(items: list[LineItem], warnings: list[str] = []) -> Quote:  # noqa: B006
    """The original handler. BUG: ``warnings=[]`` is one list shared by every call."""
    total = 0
    for item in items:
        warning = check_line(item)
        if warning is not None:
            warnings.append(warning)
        total += item.unit_price_cents * billable_quantity(item)
    return Quote(total, warnings)


def build_quote(items: list[LineItem], warnings: list[str] | None = None) -> Quote:
    """Fixed handler: each call gets its own warnings list.

    A caller may still pass a list to collect extra warnings, but the
    returned Quote holds a copy, so later changes by either side do not leak.
    """
    # WHY: None is immutable, so it is safe as a default; the list is created
    # per call, after the function was defined.
    if warnings is None:
        warnings = []
    collected = list(warnings)
    total = 0
    for item in items:
        warning = check_line(item)
        if warning is not None:
            collected.append(warning)
        total += item.unit_price_cents * billable_quantity(item)
    return Quote(total, collected)


def find_shared_mutable_defaults(function: object) -> list[str]:
    """Name the parameters of ``function`` whose default is a list, dict or set.

    A cheap check a test suite can run over every handler to stop the bug
    from coming back.
    """
    shared = []
    for name, parameter in inspect.signature(function).parameters.items():
        if isinstance(parameter.default, (list, dict, set)):
            shared.append(name)
    return shared
