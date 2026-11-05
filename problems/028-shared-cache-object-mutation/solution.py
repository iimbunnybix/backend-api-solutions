"""In-process product cache that request handlers cannot corrupt.

The catalog service cached product records in a plain dict and handed the
*same* dict object to every request. One handler applied a member discount by
writing to ``product["price_cents"]``, and another removed the internal
``cost_cents`` field before returning a public response. Those edits changed
the cached object itself, so every later request, for every user, saw the
discounted price and the missing field until the process restarted.

The fix never shares a mutable object: ``FrozenProductCache`` stores a deeply
read-only snapshot (mutation raises immediately), and ``CopyingProductCache``
hands each caller its own deep copy. Handlers build new dicts instead of
editing the one they were given.
"""
from __future__ import annotations

import copy
from types import MappingProxyType
from typing import Any, Callable, Mapping

Loader = Callable[[str], dict]
PERCENT = 100
INTERNAL_FIELDS = ("cost_cents",)


# --- the original, buggy version ---------------------------------------------
class NaiveProductCache:
    """Returns the cached dict itself, so any caller can change it for everyone."""

    def __init__(self, loader: Loader) -> None:
        self._loader = loader
        self._entries: dict[str, dict] = {}

    def get(self, sku: str) -> dict:
        if sku not in self._entries:
            self._entries[sku] = self._loader(sku)
        return self._entries[sku]


def naive_member_price_view(product: dict, discount_percent: int) -> dict:
    """Original handler: edits the product it was given (the cached object)."""
    product["price_cents"] = product["price_cents"] * (PERCENT - discount_percent) // PERCENT
    return product


def naive_public_view(product: dict) -> dict:
    """Original handler: deletes internal fields from the cached object."""
    for field in INTERNAL_FIELDS:
        product.pop(field, None)
    return product


# --- the fix, option 1: immutable snapshots ----------------------------------
def freeze(value: Any) -> Any:
    """Return a deeply read-only copy: dicts become read-only mappings, lists tuples.

    Strings, numbers, booleans and None are already immutable and are reused.
    """
    if isinstance(value, Mapping):
        frozen_items = {}
        for key, item in value.items():
            frozen_items[key] = freeze(item)
        # Nobody else holds frozen_items, so the read-only proxy cannot be bypassed.
        return MappingProxyType(frozen_items)
    if isinstance(value, (list, tuple)):
        return tuple(freeze(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return frozenset(freeze(item) for item in value)
    return value


def thaw(value: Any) -> Any:
    """Return a new, fully mutable (and JSON-serialisable) copy of a frozen value."""
    if isinstance(value, Mapping):
        plain = {}
        for key, item in value.items():
            plain[key] = thaw(item)
        return plain
    if isinstance(value, (list, tuple)):
        return [thaw(item) for item in value]
    if isinstance(value, frozenset):
        return set(value)
    return value


class FrozenProductCache:
    """Stores and returns read-only snapshots; shared safely without copying on read."""

    def __init__(self, loader: Loader) -> None:
        self._loader = loader
        self._entries: dict[str, Mapping[str, Any]] = {}

    def get(self, sku: str) -> Mapping[str, Any]:
        if sku not in self._entries:
            # Freezing makes a copy, so the loader's own object can't leak in either.
            self._entries[sku] = freeze(self._loader(sku))
        return self._entries[sku]


# --- the fix, option 2: copy on write into and read out of the cache ---------
class CopyingProductCache:
    """Gives every caller a private deep copy; callers may mutate it freely."""

    def __init__(self, loader: Loader) -> None:
        self._loader = loader
        self._entries: dict[str, dict] = {}

    def get(self, sku: str) -> dict:
        if sku not in self._entries:
            self._entries[sku] = copy.deepcopy(self._loader(sku))
        # A shallow dict(...) copy is not enough: nested lists would still be shared.
        return copy.deepcopy(self._entries[sku])


# --- handlers written for the fix: build a new dict, never edit the input -----
def member_price_view(product: Mapping[str, Any], discount_percent: int) -> dict:
    """Return a new product dict with the member price; the input is untouched."""
    if not 0 <= discount_percent <= PERCENT:
        raise ValueError("discount_percent must be between 0 and 100")
    view = thaw(product)
    view["price_cents"] = product["price_cents"] * (PERCENT - discount_percent) // PERCENT
    return view


def public_view(product: Mapping[str, Any]) -> dict:
    """Return a new dict without internal fields; the input is untouched."""
    view = {}
    for key, value in product.items():
        if key not in INTERNAL_FIELDS:
            view[key] = thaw(value)
    return view
