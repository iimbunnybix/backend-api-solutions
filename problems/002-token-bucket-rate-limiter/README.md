# 002 · One client's script floods the public API and slows it for everyone

## Problem
A partner integration runs a sync job that fires ~3,000 requests/second at
`GET /orders` for a few minutes every hour. Database CPU hits 100% and p99
latency for every other customer jumps from 120 ms to 4 s.

## Root cause
There is no per-client rate limit. A fixed "100 requests per minute" counter
was tried before but caused two new problems: bursts at the window edge
(200 requests in 2 seconds across a minute boundary are allowed) and well-
behaved clients being blocked for a whole minute after a short burst.

## Solution
A **token bucket** per API key:

- Each client has a bucket with `capacity` tokens (allowed burst size).
- Tokens refill continuously at `rate` per second.
- Each request costs one token (expensive endpoints can cost more).
- Empty bucket → `429 Too Many Requests` with a `Retry-After` header saying
  exactly when the next token will be available.

Refill is computed lazily from elapsed time, so there is no background timer
and memory is O(active clients). A lock makes it safe under concurrent requests.

## Trade-offs
- In-memory buckets only limit traffic per server; with N servers use a
  shared store (Redis + Lua script doing the same arithmetic atomically).
- Token bucket allows short bursts by design; use a leaky bucket if the
  downstream needs a perfectly smooth rate.
- Idle buckets should be evicted (LRU/TTL) to bound memory.

## Run
```bash
python -m unittest -v test_solution.py
```
