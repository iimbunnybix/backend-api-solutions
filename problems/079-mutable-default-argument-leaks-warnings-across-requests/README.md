# 079 · Customers saw warnings about other companies' products on their quotes, and the list got longer all day

## Problem
A B2B ordering API returns a price quote for a basket. Each quote carries a
`warnings` list, such as "ACME-CRATE: quantity 500 capped at 100" when a line
exceeds the self-service limit, or "free item, check the price list".

After a release that moved validation into a helper, customers started
reporting quotes with warnings about SKUs they had never ordered, some
naming other customers' private catalogue items. The list grew through the
day and was empty again after each deploy or worker restart. Besides
confusing buyers, it leaked other tenants' product codes, and the mobile app
blocked checkout whenever `warnings` was non-empty.

## Root cause
The helper was written as `def build_quote(items, warnings=[])`. Python
evaluates a default value **once, when the `def` runs**, not on each call.
Every request that did not pass its own list appended to that one shared
list object, which lived as long as the worker process.

In the tests, five bulk orders in a row leave five warnings in the fifth
response, and a clean order placed after a bulk order still shows the bulk
order's warning. `original_build_quote.__defaults__` shows the list that
everyone shares.

## Solution
- Default to `None` and create a new list inside the function
  (`if warnings is None: warnings = []`). `None` is immutable, so sharing it
  is harmless.
- Copy any list the caller passes and return the warnings in the `Quote`, so
  neither side can change the other's list afterwards.
- `find_shared_mutable_defaults(function)` uses `inspect.signature` to list
  parameters whose default is a list, dict or set. A one-line test over all
  handlers stops the pattern from returning; linters flag the same thing
  (flake8-bugbear `B006`, pylint `W0102`).

## Trade-offs
- The same trap applies to any mutable default: dicts used as caches,
  dataclass fields (which is why `dataclasses` requires `default_factory`)
  and module-level lists that handlers append to.
- Copying the caller's list costs a small allocation per request; it is
  negligible next to the request itself.
- Sometimes a shared default is used on purpose as a cache. Make that
  explicit with `functools.lru_cache` or a named module-level object with a
  size limit, never a hidden default argument.

## Run
```bash
python -m unittest -v test_solution.py
```
