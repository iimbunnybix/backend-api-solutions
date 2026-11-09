# 015 · Checkout returned 503 for most shoppers because the optional recommendations service got slow

## Problem
A checkout API runs 40 request workers shared by every route. Each second it
receives about 10 payment requests (≈0.5 s each) and 5 requests that also call
the recommendations service (normally 50 ms). One evening recommendations
degraded and started taking 30 s per call. Within seconds every worker was
blocked waiting on it, and payment requests were rejected with 503 although the
payment provider was perfectly healthy. In the simulation in `solution.py`
(60 seconds of that traffic) only **65 of 600** payment requests were accepted;
the rest of the revenue for that window was lost.

## Root cause
All routes draw from one shared pool, and nothing limits how many workers a
single dependency can hold. A slow dependency does not fail fast — it *holds*
workers for the full timeout — so it gradually absorbs the whole pool. This is
different from a circuit breaker problem (problem 009): even before any
breaker trips, the shared pool is already exhausted.

## Solution
Add a **bulkhead** per risky dependency: a counter of in-flight calls with a
hard limit (8 of the 40 workers for recommendations here). A request first
checks its compartment; if the compartment is full the dependency call is
skipped and never ties up a shared worker for 30 s:
- for an *optional* dependency (listed in `fallback_seconds`) the request is
  still served without it, holding a worker only for its own short work
  (50 ms here) and returning `accepted_without_dependency`;
- for a *required* dependency it is rejected immediately
  (`rejected_bulkhead_full`) so the caller can fail fast.

Dependencies without a limit share the remaining pool as before.

With the same traffic and recommendations marked optional, payments get
**600 of 600** accepted, and all 300 page requests are served: 16 with
recommendations (what fits in the 8-slot compartment over the minute) and 284
without them. Without a fallback, those 284 page requests would be rejected
fast instead. When recommendations are healthy, both servers accept
everything (300 and 600).

In production this is a per-dependency semaphore (or a separate thread pool)
around the client call, as in Resilience4j's `Bulkhead`.

## Trade-offs
- Limits must be sized from real concurrency (Little's law: rate × latency);
  too small and you reject healthy traffic during normal spikes.
- Static partitions waste capacity when a compartment is idle; adaptive
  concurrency limits (e.g. Netflix's concurrency-limits) adjust automatically.
- A bulkhead caps the damage but still lets slow calls hold up to the limit;
  combine it with tight timeouts and a circuit breaker.
- Separate thread pools per dependency isolate better than semaphores but cost
  memory and context switches.

## Run
```bash
python -m unittest -v test_solution.py
```
