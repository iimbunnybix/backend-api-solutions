# 029 · Every API endpoint returned "connection pool exhausted" whenever the payment provider got slow

## Problem
The shop's checkout API runs with a database connection pool of 10 per pod
and takes about 20 checkouts per second per pod at the evening peak. One
Friday the external payment provider's response time went from its usual
0.3 s to about 3 s. Checkout did not just get slower: within seconds every
endpoint on the pod, including product pages and order history, started
returning 503 "connection pool exhausted". Database dashboards showed
sessions "idle in transaction" and other writers waiting on locks. The
database itself was nearly idle. For the 40 minutes the provider was slow,
almost no orders got through, and the on-call engineer's restarts did nothing.

## Root cause
`naive_place_order` opened a transaction, inserted the order, **called the
payment provider over HTTP inside the transaction**, then marked the order
paid and committed. The connection and the transaction's write lock stayed
checked out for the whole network call (the test sees `in_transaction` is
true while the provider runs).

By Little's law, connections in use = arrival rate × hold time:

| Provider latency | Hold time per checkout | Connections needed at 20/s |
|---|---|---|
| 0.3 s (normal) | 0.305 s | 6.1 (fits in 10) |
| 3 s (degraded) | 3.005 s | 60.1 (pool of 10 is gone) |

A pool of 10 can serve only about 3.3 checkouts per second at 3 s per hold.
The rest of the requests, checkout or not, queue for a connection and time out.
The open transactions also hold row and table locks, so other writers wait
too. In `ReproduceProblem`, a second customer's checkout fails with
`PoolExhausted` while the first is waiting on the provider.

Problem 005 was a *leaked* connection (never returned). Here every
connection is returned. It is just held across slow work that does not need it.

## Solution
Never hold a connection or transaction across a network call to another
system. `place_order` splits the work into two short transactions:

1. `record_pending_order`: insert the order as `PENDING` and commit. The
   intent is now durable and visible before any money moves.
2. Call `provider.charge` with **no connection checked out**. The order id is
   the idempotency key, so a retry can't charge twice (see problem 001).
3. `record_payment_outcome`: move the order to `PAID` or `FAILED`, guarded by
   `WHERE status = 'PENDING'` so a reconciler that already settled it is not
   overwritten.

Each transaction now holds a connection only for its own SQL (5 ms
simulated), 0.01 s per checkout in total, or 0.2 connections in use at 20/s,
whatever the provider's latency. The same pool could carry about 1,000
checkouts per second. With a single-connection pool, a second customer can
check out while the first one waits on the provider.

If the process dies between the charge and step 3, the order stays `PENDING`.
`find_stuck_pending_orders` lists those older than a threshold so a
reconciler can ask the provider (by idempotency key) what happened and
finish the order.

## Trade-offs
- The order now has visible intermediate states. The UI, emails and
  reporting must handle `PENDING`, and a reconciler job is required. Without
  it, crashed checkouts stay pending forever.
- Without the single transaction, "charged" and "order paid" are no longer
  atomic. Correctness relies on the idempotency key plus reconciliation (a
  small saga), not on a database rollback. A rollback could never undo an
  external charge anyway.
- Anything checked inside the first transaction (stock, price) can change
  before the second one. Reserve stock in step 1 and release it on failure
  instead of re-reading it.
- The provider is still slow for the customer. Pair this with a client
  timeout, a deadline (problem 008) and a circuit breaker (problem 009) so slow
  calls don't pile up threads instead of connections.
- A bigger pool only moves the limit: at 3 s per hold you would need over
  60 connections per pod, which the database cannot afford across all pods.

## Run
```bash
python -m unittest -v test_solution.py
```
