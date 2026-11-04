# 005 · Checkout API hangs for every user a few minutes after a spike in validation errors, and only a restart fixes it

## Problem
The checkout service runs 8 workers per pod, each with a database pool of 10
connections. After a mobile release started sending some malformed order IDs,
checkout requests began timing out across the whole fleet. Database CPU was
idle, yet every worker thread was blocked inside `pool.get()`. Restarting the
pods "fixed" it for a few minutes, then it happened again. Every hang meant
failed checkouts for all users, not just those on the buggy app version.

## Root cause
The handler checked out a connection with `conn = pool.get()` and called
`pool.put(conn)` only at the end of the happy path. Any exception between the
two (here, `ValueError` from input validation) skipped the return, so each bad
request **leaked one connection**. The pool had no acquire timeout, so once
`max_size` connections had leaked, every later request (including valid
ones) blocked forever.

The test reproduces it: with `max_size=5`, five bad requests leak all five
connections and the next 10 valid requests all fail to get a connection.

## Solution
`ConnectionPool.connection()` is a context manager that owns the full
lifecycle:

- **Always released.** `try/finally` returns the connection on success,
  exception, or early `return`. With alternating good/bad requests (100 total),
  50 succeed, 50 fail validation, and `in_use()` ends at 0.
- **Rolled back before reuse.** A failed transaction is rolled back so the next
  request never inherits half-done work. If rollback itself fails, the
  connection's state is unknown, so it is closed and its slot freed.
- **Acquire timeout.** `acquire()` waits on a condition variable up to a
  deadline, then raises `PoolTimeout` (map to HTTP 503 + `Retry-After`). A
  saturated pool now degrades into fast errors instead of hung workers.
- **Hard cap under concurrency.** 16 threads × 50 requests on a 4-connection
  pool never exceed 4 in use.
- **Leak detection.** Each checkout records its time and stack;
  `leak_report(older_than_s=30)` points straight at the code that took the
  connection and never returned it. Export the count as a metric and alert on it.

## Trade-offs
- The timeout trades hangs for errors. Set it below the upstream request
  timeout, or the client gives up before the pool does.
- Capturing a stack on every checkout costs a few microseconds; sample it, or
  enable it only when the leak metric is non-zero, on very hot paths.
- Pool size does not fix leaks; a larger pool only delays the outage. It also
  multiplies across pods, so total connections must stay under the database's
  `max_connections` (or put PgBouncer/RDS Proxy in front).
- Real pools (SQLAlchemy, HikariCP, psycopg_pool) provide most of this; the
  important part is configuring `pool_timeout`/leak detection and using their
  context managers, not hand-rolling `get`/`put`.

## Run
```bash
python -m unittest -v test_solution.py
```
