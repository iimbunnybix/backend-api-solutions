# 019 · Shoppers on one store saw another store's prices after a "harmless" response-caching release

## Problem
A white-label commerce platform serves hundreds of shops (tenants) from one
API. To cut database load on `GET /catalog/prices`, the team added a response
cache in front of the handler. Within minutes, support tickets arrived:
shoppers of one shop were seeing another shop's price list, German shoppers
got English product text, and customers who switched to USD still saw euro
amounts. Some orders were placed at the wrong price and had to be refunded.
Nothing failed and no error was logged; the cache was simply serving the
right path with the wrong content.

## Root cause
The cache key was the URL path only. The response actually depends on more
than the path: the tenant (from the auth token, not the URL), the
`Accept-Language` header and the `currency` query parameter. Every request
for `/catalog/prices` mapped to the same key, so whichever tenant filled the
entry first decided what everybody got until it expired. The
`ReproduceProblem` tests show shop B receiving `"prices for shop-a in en/EUR"`.

This is the same reason HTTP has the `Vary` response header: a cache has to
know which request inputs change the response.

## Solution
- Each cacheable route declares a `VarySpec`: whether it depends on the
  tenant, which headers and which query parameters.
- `build_cache_key()` builds the key from the path plus exactly those
  inputs. Header names are lower-cased (HTTP header names are
  case-insensitive), names are sorted so order doesn't matter, a missing
  value is recorded as missing (so "no header" and "empty header" differ),
  and every value is length-prefixed so values containing the separator
  cannot be shifted to collide with another request. The result is hashed
  with SHA-256 to keep keys short.
- A route with **no** declared `VarySpec` is not cached at all. Failing safe
  (an extra database read) is much cheaper than leaking another tenant's
  data.
- Headers that are not listed (such as `X-Request-Id`) do not split the
  cache, so the hit rate stays high.

## Trade-offs
- The vary list must be kept up to date. If someone makes the handler read a
  new header and forgets to add it, the bug returns. A stronger option is to
  give the handler a request view that only exposes the declared inputs, so
  an undeclared read fails in tests.
- Each extra vary dimension multiplies the number of cache entries
  (tenants × languages × currencies) and lowers the hit rate. Normalising
  values first (for example mapping `Accept-Language: de-DE,de;q=0.9` to
  `de`) keeps the key space small.
- Per-user data (cart, personalised prices) should not go into a shared
  response cache at all; cache the shared parts and assemble per user.
- For CDN caching the same idea is expressed with the `Vary` header and
  tenant-specific hostnames or paths.

## Run
```bash
python -m unittest -v test_solution.py
```
