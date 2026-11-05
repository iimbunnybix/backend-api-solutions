# 011 · Dishes the kitchen marked "sold out" kept showing as available in the app for a whole day

## Problem
A food-delivery menu API serves dish availability from Redis using
cache-aside with a 24-hour TTL: on a miss it reads the row from Postgres and
writes it to the cache; when a restaurant marks a dish sold out, the API
updates the row and deletes the cache key. During the Friday dinner rush a
handful of dishes stayed "available" in the app for the rest of the day even
though the restaurant dashboard (which reads Postgres directly) showed them
sold out. Customers kept ordering them, restaurants rejected the orders, and
each rejection meant a refund and a support contact. Deleting the keys by hand
fixed each dish, but new ones appeared every busy evening.

## Root cause
**A slow cache-miss read wrote an old row back into the cache *after* the
update had invalidated it.** The sequence:

1. A request misses the cache and reads the row: `available`.
2. Before it writes the cache, it stalls (GC pause, slow network hop, CPU
   throttling at peak load).
3. The restaurant marks the dish sold out: the row becomes `sold_out` and the
   cache key is deleted.
4. The stalled request resumes and runs `SET dish:42 available EX 86400`.

Nothing deletes the key again, so the stale value is served until the TTL
expires. The tests reproduce it: after the interleaving, the cache still
returns `available` 23 hours later and only corrects itself at the 24-hour
mark. The race window is small, which is why it only showed up under peak
load and on frequently read, frequently changed rows.

## Solution
`VersionedMenuService` in `solution.py`:

1. **Version every row.** Each update increments a `version` column.
2. **Versioned, atomic cache writes.** A miss fills the cache with
   `set_if_newer(key, value, version)`: it only replaces an entry with a lower
   version. In Redis this is a short Lua script (compare and set in one step);
   memcached can do the same with CAS.
3. **Tombstones instead of deletes.** An update replaces the key with a
   tombstone that remembers the new version for `tombstone_ttl` (60 s).
   A reader that read the old row carries the old version and is rejected; a
   reader that read the new row is accepted and refills the cache.
4. Invalidations never lower the version, so a delayed invalidation from an
   older write cannot reopen the window.

The tests cover the original interleaving, two updates during one slow read,
out-of-order invalidations, refilling after a tombstone (the next read is a
cache hit again), and a real reader thread that is paused (with
`threading.Event`s) between its DB read and its cache set while the main thread
commits an update: the naive service is left with a stale live entry, the
versioned service is not.

## Trade-offs
- **The tombstone TTL is the safety margin.** A reader that stalls for longer
  than it can still write a stale value (a test shows this). Set it well above
  your request timeout; it costs only a tiny key per recent update.
- Requests already in flight during the update may still *return* the old
  value once. The fix stops the cache from *keeping* it.
- Alternatives: **leases** (memcached / Facebook's "Scaling Memcache" design:
  a miss gets a lease token that an invalidation cancels, which also limits
  stampedes); **delayed double delete** (delete again a few seconds later:
  simple but timing-based); **CDC-driven invalidation** from the database log
  (Debezium), which orders invalidations with the writes but adds a pipeline.
- **Shorter TTLs** shrink the damage but not the race, and cost more DB reads.
- If the data must never be stale (stock you are about to charge for), read
  from the database or write-through at checkout and use the cache only for
  browsing.

## Run
```bash
python -m unittest -v test_solution.py
```
