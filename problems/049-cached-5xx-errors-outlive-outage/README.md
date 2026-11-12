# 049 · Product pages kept showing "503 Service Unavailable" for five minutes after a 30-second outage was over

## Problem
A catalog API sits behind a reverse-proxy cache (the same idea applies to a
CDN or an app-level response cache). To raise the hit rate, the team added
one global middleware that sets `Cache-Control: public, max-age=300` on every
response.

A database failover made the origin return `503` for 30 seconds. Dashboards
showed the origin healthy again right after, but shoppers kept getting error
pages for some products for almost five more minutes, and "is the site down?"
tickets kept coming in. Purging the whole CDN by hand was the only quick fix,
and it hit the origin with a burst of cold traffic.

In `simulate_outage` (300 product URLs, each requested once per second, URL i
first requested at second i, origin down from second 600 to 630), the
original setup leaves **30 URLs** broken after recovery and serves **8,535**
error responses after the origin is healthy, the last one at second **928**,
298 seconds after recovery.

## Root cause
The cache stored the `503` exactly like a good page, because the middleware
told it to: `max-age=300` was added without looking at the status code. Every
URL whose cached copy expired during the 30-second window refetched, got the
error and pinned it for the full five minutes. Nothing in the proxy treated a
server error differently, so the short outage was stretched to the length of
the TTL. (Problem 016 caches *not found* on purpose; this one is about
accidentally caching *server errors*.)

## Solution
Fix both sides, so either one alone prevents the long tail:

1. **Origin middleware only marks cacheable statuses as cacheable**
   (`fixed_cache_headers`). Statuses in `CACHEABLE_STATUSES` (200, 203, 204,
   300, 301, 308, 404, 410) get `max-age=300, stale-if-error=600`; anything
   else, including every 5xx, gets `no-store`. With only this fix the last
   error is served at second 629, inside the outage, and **0** after it.
2. **The proxy honours `no-store` and caps error TTLs** (`storable_entry`).
   If an origin still sends a long `max-age` on a 5xx, the proxy keeps it for
   at most 5 s (`ERROR_MAX_AGE_CAP_SECONDS`) and never serves it stale. With
   the original origin and the fixed proxy, errors stop by second 633.
3. **`stale-if-error` serves the last good copy during the outage**
   (RFC 5861). When a refetch returns a 5xx and an expired good copy is
   still within its `stale-if-error` window, the proxy returns that copy and
   keeps it. With both fixes clients see **0** errors, even during the
   30 seconds the origin was down.

## Trade-offs
- `stale-if-error` serves content up to 10 minutes past its TTL during an
  outage. That is fine for product pages, but not for stock levels or prices
  that must be exact; give those routes a short or no `stale-if-error`.
- With `no-store` on errors, every request during an outage reaches the
  origin. The 5-second error cap (or request collapsing in the proxy) limits
  the load on a struggling origin; a longer cap protects it more but keeps
  errors around for longer.
- `CACHEABLE_STATUSES` includes 404. Caching 404 for the full TTL means a
  newly created product can stay "not found"; give 404 a shorter TTL (see
  problem 016).
- Many CDNs have their own default TTLs for error statuses that apply even
  without `Cache-Control`; check the CDN's settings, not only the origin's
  headers.

## Run
```bash
python -m unittest -v test_solution.py
```
