# 001 · Payment API double-charges customers when the mobile app retries

## Problem
A checkout service calls `POST /payments`. On slow mobile networks the app
times out after 10 s and retries. The first request had actually succeeded,
so the customer is charged twice. Support sees ~40 duplicate-charge
complaints a day, each needing a manual refund.

## Root cause
The endpoint is not **idempotent**. The server cannot tell "a retry of the
same intent" from "a new payment", so every request creates a new charge.
Two retries can also arrive *concurrently*, so a naive "check if paid, then
pay" has a race window.

## Solution
Client sends an `Idempotency-Key` header (a UUID generated once per checkout).
The server:

1. Atomically **claims** the key (status `IN_PROGRESS`) before doing any work.
2. Executes the charge exactly once and **stores the response** under the key.
3. Any retry with the same key gets the **stored response** — no new charge.
4. A concurrent duplicate while the first is still running gets `409 Conflict`
   (client backs off and retries).
5. The same key with a **different request body** is rejected (`422`) — it is a
   client bug, not a retry.
6. Keys expire after a TTL (24 h) so storage stays bounded.

`solution.py` implements this with an in-memory store guarded by a lock; in
production the store is Redis (`SET key value NX EX 86400`) or a DB table
with a unique constraint on the key.

## Trade-offs
- Storing responses costs memory/disk; the TTL bounds it.
- If the process crashes mid-charge, the key stays `IN_PROGRESS` until it
  expires; real systems reconcile with the payment provider's own idempotency.
- Fingerprinting the body catches key reuse bugs but needs a canonical form.

## Run
```bash
python -m unittest -v test_solution.py
```
