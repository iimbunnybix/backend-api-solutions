# 071 · Checkout pods grew in memory until OOMKilled, and the "cached" catalog service saw no drop in traffic

## Problem
A grocery checkout API prices every cart by calling the internal catalog
service once per SKU. To cut that traffic, an engineer added
`@functools.lru_cache` to the `price_for(self, sku)` method of
`PricingService`, the object the API creates for each request.

After the deploy, the catalog team saw **no reduction at all** in calls. At
the same time, checkout pods grew steadily in memory and were OOMKilled every
few hours, dropping in-flight checkouts. Each restart "fixed" it for a while,
so the leak went unnoticed for days.

In the test replay, 1,000 checkouts of a 3-item cart:

| | catalog calls | finished requests still in memory | cache entries |
|---|---|---|---|
| `lru_cache` on the method | 3,000 | 1,000 (10,000,000 bytes of bodies) | 3,000 |
| shared SKU cache (fix) | 3 | 0 | 3 |

## Root cause
`lru_cache` on a method is stored on the **class's function**, so it is shared
by every instance, and its key is the full argument tuple, **including
`self`**:

- A new request means a new `self`, so the key `(service, "apple")` never
  matches an entry made by an earlier request. Only repeats inside one request
  can hit.
- The cache keeps a strong reference to each key, so every `PricingService`,
  and through it the request and its body, can never be garbage-collected.
  With `maxsize=None` nothing is ever evicted, so memory grows with traffic.

(Python's documentation and linters such as flake8-bugbear's B019 warn about
exactly this.)

## Solution
- `SharedPriceCache` is created **once per process** and keyed by SKU only, so
  all requests share entries: 3 catalog calls instead of 3,000.
- Each entry stores when it was fetched; a price older than
  `PRICE_TTL_SECONDS` (60 s) is fetched again, so price changes still show up.
- A maximum size (`MAX_CACHED_PRICES`) with least-recently-used eviction
  (`OrderedDict.move_to_end` / `popitem(last=False)`) bounds memory.
- `PricingService` is still per request but only **borrows** the cache, so
  nothing refers to it after the request finishes and it is collected.

## Trade-offs
- A price can be up to 60 s stale. For checkout that may be fine if the final
  order is re-priced server-side; otherwise invalidate entries on a
  "price changed" event, or use a shorter TTL.
- The cache is per process: 50 pods still make 50 catalog calls per SKU per
  TTL. A shared cache (Redis) cuts that further at the cost of a network hop.
- This class is not thread-safe. Under a threaded server, guard it with a
  `threading.Lock`, and consider single-flight so many requests missing the
  same SKU at once do not all call the catalog.
- If per-request caching was the intent, store a plain dict on the instance
  (`self._prices`) instead; it dies with the request. `functools.cached_property`
  is another per-instance option for values without arguments.

## Run
```bash
python -m unittest -v test_solution.py
```
