# 043 · Order API froze completely at the lunch peak with an idle database, and every request ended in "pool timeout"

## Problem
A food-ordering API runs each pod with a database connection pool of 10 and
a 30-second checkout timeout. It had run for months without trouble and
passed its load test, which ramped traffic up gradually. One lunchtime a push
notification campaign sent a sharp burst of orders. Within a second, every
order request on the affected pods stopped making progress. The database was
almost idle (10 sessions "idle in transaction", no locks waited on), CPU on
the pods was near zero, and 30 seconds later every request failed at once
with `QueuePool limit of size 10 overflow 0 reached, connection timed out, timeout 30`. The retries
from the apps re-created the burst, so the pods kept freezing in 30-second
cycles until traffic dropped. Orders were lost for the whole peak, and
restarting pods did not help.

## Root cause
**One request needed two connections from the same pool at the same time.**
The order handler checked out a connection and began a transaction, then
called a shared helper, `load_customer_tier(customer_id)`, that did its own
`with pool.connection()` to read the customer's loyalty tier. The handler held
its first connection while waiting for the second.

That is fine while fewer requests than connections are in flight: some
connection is always free for the helper. But if 10 requests start together,
each takes one connection for its transaction, the pool is empty, and all 10
then wait for a second connection that only another waiting request could
return. Nobody can move: a deadlock inside the pool, invisible to the
database's own deadlock detector because no database lock is involved. A
pool timeout turns the hang into errors, but cannot let any request finish.

The simulation in `solution.py` runs bursts of simultaneous requests against
a pool of 10 (`run_burst`, one step per tick, it stops when a tick makes no
progress):

| Burst | Original handler | Fixed handler |
|---|---|---|
| 5 requests | 5 completed | 5 completed |
| 9 requests | 9 completed (15 ticks) | 9 completed (5 ticks) |
| 10 requests | **0 completed, 10 stuck**, each holding 1 connection | 10 completed |
| 30 requests | **0 completed, 30 stuck** | 30 completed |

So the bug appears exactly when concurrency reaches the pool size, which a
gradual load test never hits all at once.

## Solution
1. **One connection per request.** The helper now takes the caller's
   connection as a parameter, `load_customer_tier(conn, customer_id)`, and
   runs its query inside the request's transaction. A request can only ever
   hold one connection, so it never waits while holding one, and any burst
   simply queues and completes (`place_order_steps`).
2. **Make the mistake loud.** `ConnectionPool(strict=True)` raises
   `NestedCheckoutError` when a request that already holds a connection asks
   for another. With it enabled in tests, the original handler fails with a
   *single* request, long before production concurrency could expose it.
   In a real service the "request id" is the current task or thread, kept
   in a context variable for the request's lifetime.

## Trade-offs
- Passing the connection through changes helper signatures, and helpers now
  run inside the caller's transaction (they see its uncommitted writes and
  share its rollback). That is usually what you want; if a helper must
  commit independently (an audit log that survives rollback), give it a
  **separate pool** sized for that work, or run it after the first
  connection is released.
- Making the pool larger (e.g. `2 × workers`) only moves the threshold:
  any burst of `pool_size` simultaneous requests still deadlocks, and the
  extra connections count against the database's `max_connections`
  (see problem 036).
- Strict mode needs a notion of "the current request" (thread or task id),
  and legitimate two-connection code (e.g. reading from a replica pool while
  writing to the primary) must use two different pools.
- A pool checkout timeout is still needed, but it only converts the hang into
  errors; it is a safety net, not a fix.

## Run
```bash
python -m unittest -v test_solution.py
```
