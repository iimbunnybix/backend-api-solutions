# 016 · Primary database CPU spiked during a scrape of product IDs that don't exist

## Problem
A product-detail API sits behind a cache that normally absorbs almost all
reads. A scraper started requesting product IDs that do not exist (old,
deleted and guessed SKUs) every few seconds. Cache hit rate collapsed, the
primary database's CPU climbed, and real product pages slowed down.

In the simulation in `solution.py` the scraper asks for 500 missing IDs every
6 seconds for 2 minutes (10,000 requests). The original read path sent
**all 10,000** to the database.

## Root cause
The read path only caches results that exist (`if product is not None:
cache.set(...)`). A "not found" answer is never remembered, so every lookup
of a missing ID is a guaranteed cache miss. This is *cache penetration*: the
cache is bypassed, not overloaded. It is different from a cache stampede
(many requests for one popular key expiring at once).

## Solution
Cache the absence as well:

1. When the database returns nothing, store a `NOT_FOUND` marker object under
   the key with a short TTL (60 s, vs 600 s for real products).
2. On read, a `NOT_FOUND` hit returns "not found" without touching the
   database. A separate marker object is used because `None` already means
   "not in cache".
3. When a product is created, its cache key is deleted, so it is visible
   immediately instead of after the marker expires.
4. A read that overlaps a create must not write its stale "not found" after
   the create's invalidation. The cache keeps a per-key invalidation counter;
   the reader notes it before querying and writes `NOT_FOUND` only if it has
   not changed (in production this check-and-set must be atomic, e.g. a Redis
   Lua script or memcached lease).

With the same scraper traffic, database queries drop from **10,000 to 1,000**:
each missing ID is queried once, then once more after its 60 s marker expires.

## Trade-offs
- Negative entries take cache memory; an attacker with random IDs can fill
  the cache with them. Bound them (separate small LRU) or reject IDs that fail
  format validation before the cache.
- A write path that forgets to invalidate shows "not found" for up to the
  negative TTL; keep that TTL short.
- For huge ID spaces of random guesses, a Bloom filter of existing IDs in
  front of the cache (see system-design-solutions 008) rejects most misses
  without storing one entry per guess.
- Rate-limiting the scraper treats the symptom for one client only.

## Run
```bash
python -m unittest -v test_solution.py
```
