# 023 · Database CPU spikes to 100% exactly five minutes after every deploy, then again every five minutes

## Problem
The product catalogue API caches each product row in Redis. To avoid a cold
cache after a deploy, the release job warms all hot products in one loop,
each with `TTL = 300 s`. Five minutes after every deploy the database CPU
pinned at 100%, p99 latency on product pages jumped from ~40 ms to several
seconds, and the checkout service (which shares the database) started timing
out. The spike came back every five minutes until traffic patterns slowly
pushed the expiries apart, so a deploy during peak hours cost a measurable
slice of revenue each time.

## Root cause
Every entry was written within the same second with the **same fixed TTL**,
so every entry also **expired in the same second**. All hot keys missed at
once and each miss went to the database. The reloaded entries were again
written together with the same TTL, so the synchronized expiry repeated every
period: the cache turned a steady read load into a periodic thundering herd.

The simulation in the tests warms 1,000 hot keys at t = 0 and reads every key
once per second: with the fixed TTL, **all 1,000 database loads land in second
300 and again in second 600**, and no loads happen in between.

## Solution
`make_jittered_ttl()` gives every write a random TTL drawn uniformly from
`base × (1 ± 10%)`, i.e. 270–330 s for a 300 s base.

- Expiries (and therefore reloads) are spread over a 60 s window instead of
  one second. With a seeded generator and the same 1,000 keys, the busiest
  second has **28 loads instead of 1,000** (the even-spread average is
  1000 / 60 ≈ 17), and all 1,000 first reloads fall between second 271 and 330.
- The total number of loads is unchanged; only their timing moves.
- Each reload picks a fresh random TTL, so later waves spread out even more
  instead of re-synchronizing (the second wave runs from second 545 onward,
  peak 26).
- A `MINIMUM_TTL_SECONDS` floor stops large jitter on a tiny TTL from producing
  zero or negative TTLs, and the jitter fraction is validated.
- The random generator is injected, so tests are deterministic.

## Trade-offs
- Jitter barely changes the total load: with ±10% the mean TTL stays 300 s and
  the refresh rate rises by only about 0.3% (the mean of 1/TTL is slightly
  above 1/300). Wider jitter spreads load more but makes freshness less
  predictable; pick the fraction from how much spread the database needs.
- Jitter only fixes **synchronized** expiry. A single very hot key that expires
  still sends every concurrent request to the database at once; that needs
  request coalescing / single-flight (see system-design 003) or probabilistic
  early refresh (XFetch), where readers refresh a key slightly before it expires
  with a probability that grows as expiry approaches.
- An alternative is to warm the cache gradually (rate-limited warm-up) or to
  have a background refresher rewrite entries before they expire; both need
  more machinery than one random number per write.
- Staleness bound becomes `base × (1 + jitter)` instead of `base`; if a
  business rule needs "never older than 300 s", jitter downward only
  (e.g. 240–300 s).

## Run
```bash
python -m unittest -v test_solution.py
```
