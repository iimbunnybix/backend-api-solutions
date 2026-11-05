# 028 · After one member checked out, every shopper saw the member price, and it dropped further with each new member

## Problem
The catalog service keeps an in-process cache of product records so that the
product page avoids a database call per request. A release added two small
features. Logged-in members got a 10% discount, and the public product API
hid the internal `cost_cents` field. Within minutes of the deploy, guests
were seeing member prices. Each member visit cut the price by another 10%
(2000 → 1800 → 1620 cents in the test). The admin tool also showed products
with no cost price. Restarting the pods "fixed" it for a few minutes. Several
hours of orders were taken at the wrong price before someone connected the
problem to the cache.

## Root cause
`NaiveProductCache.get()` returned the **cached dict itself**. Python passes
objects by reference, so every request got the same object, and:

- `naive_member_price_view` wrote `product["price_cents"] = ...` into the
  cached object, so the discount became the price for everyone and compounded
  on every member request.
- `naive_public_view` called `product.pop("cost_cents")` on the cached
  object, so internal callers lost the field too.
- Even `cache.get(sku)["tags"].append(...)` changed the shared nested list.

Each handler looked correct in isolation and passed its own unit tests. The
bug only appears when the object is shared, which is why code review missed it.

## Solution
Never hand out a mutable object that the cache still holds. Two options are
implemented:

1. **Immutable snapshots (`FrozenProductCache`).** On load, `freeze()`
   rebuilds the record deeply: dicts become `types.MappingProxyType`
   (read-only views), lists become tuples, sets become frozensets. Every
   caller shares the same snapshot with no copy cost. A handler that tries to
   mutate it fails right away with `TypeError`/`AttributeError` instead of
   silently corrupting data, and the test shows the old discount handler
   crashing while the cache keeps the right price. Because `freeze` builds new
   containers, later changes to the loader's object cannot leak in either.
2. **Copy on read (`CopyingProductCache`).** Store a deep copy and return a
   new `copy.deepcopy` on every `get`. Existing handlers that mutate keep
   working unchanged. A shallow `dict(...)` copy is not enough, because nested
   lists would still be shared.

Handlers are rewritten to build a **new** dict (`member_price_view`,
`public_view`) instead of editing their input. They work with either cache.

## Trade-offs
- **Copy cost.** Deep copying on every read costs CPU and memory in
  proportion to the record size, on the hottest path. That is fine for small
  records but adds up for large ones (catalog trees, config blobs). Frozen
  snapshots cost one copy per load and nothing per read.
- **Frozen values are not plain dicts.** `json.dumps` raises `TypeError` on a
  `MappingProxyType` (the test checks this), and code that checks
  `isinstance(x, dict)` or `list` behaves differently. Use `thaw()` at the
  serialization boundary, or use a JSON encoder that accepts mappings.
- `MappingProxyType` is only read-only if nobody keeps the underlying dict.
  `freeze` builds a dict nobody else holds. Wrapping an existing dict would
  not protect it.
- `@dataclass(frozen=True)` with tuple fields is a cleaner option for typed
  records, and it also protects attributes. It still does not freeze a list
  stored inside it, so nested fields need the same care.
- Primitive values (str, int) are already immutable. Custom objects inside a
  record are passed through by `freeze` unchanged and stay mutable.
- Out-of-process caches (Redis, memcached) don't have this bug, because every
  read deserializes a fresh object. The price is network and decode cost on
  every read, which is why there was an in-process cache in the first place.

## Run
```bash
python -m unittest -v test_solution.py
```
