# 041 · After a routine database failover, the API kept returning "server closed the connection unexpectedly" until every pod was restarted

## Problem
A food-delivery order API runs 30 pods, each with a pool of 10 PostgreSQL
connections, against a managed primary with a hot standby. During a planned
maintenance window the provider failed over to the standby. The database
itself was back within about 30 seconds, but the API was not: order
placement, restaurant menus and courier updates kept failing with
`server closed the connection unexpectedly`, and writes that did go through
failed with `cannot execute INSERT in a read-only transaction`. The error rate
stayed high for many minutes and only dropped to zero once the on-call
engineer rolled every pod. Thousands of orders failed during the dinner rush,
and the incident review asked why a 30-second database event caused a long
API outage.

## Root cause
**The pool trusted every idle connection.** It kept connections in a list and
handed the top one out without checking it. After the failover:

- every connection to the restarted server was dead on the server side, but
  the client only found out when it sent a query;
- connections to the old primary that survived the switch were still open,
  but that server was now a read-only standby, so a cheap ping would succeed
  while every write failed;
- the request code returned the connection to the pool in a `finally` block
  whatever the error was, so dead connections went straight back in and were
  handed to the next request.

The reproduction keeps 10 idle connections, restarts the server and sends 50
queries: **all 50 fail**, and the pool never recovers. After a failover, all
50 writes fail against the read-only old primary.

This is different from problem 005 (connections leaked, so the pool ran dry)
and problem 036 (too many connections for the database). Here the pool has
the right number of connections, but they point at a server that is gone.

## Solution
`ValidatingPool` in `solution.py` (the original is kept as `NaivePool` and
`naive_run_query`):

1. **Validate on checkout, but only when idle for a while.** A connection idle
   longer than `IDLE_PING_THRESHOLD_SECONDS` (30 s) gets a `SELECT 1` ping
   before it is handed out; if the ping fails it is closed and the next one is
   tried, and a fresh connection is opened when none are left. A connection
   used within the last 30 s is handed out without a ping, so busy pools add
   no round trip. After a restart with all connections idle, **0 of 50**
   queries fail.
2. **Discard on error, and distrust the rest.** `run_query` treats
   `ConnectionBroken` and `ReadOnlyServer` as "never reuse": the connection is
   closed instead of returned. One such error also bumps the pool's epoch, so
   every connection opened before it is discarded at checkout. A restart
   inside the 30 s window costs **1 failed request** per pool, after which all
   10 old connections are discarded and the pool reconnects. Ordinary errors
   (bad SQL, constraint violations) still return the connection.
3. **Maximum lifetime.** Connections older than `MAX_LIFETIME_SECONDS`
   (30 min) are closed on checkout or release, even if they look healthy.
   This is what fixes the failover case: the old primary still answers
   `SELECT 1`, so only a lifetime limit (or a write error) moves the pool to
   the new primary within a bounded time.

The tests use a fake clock and a fake two-node cluster whose `restart()` and
`fail_over()` behave like the incident.

## Trade-offs
- A ping costs a round trip. The idle threshold keeps it off the hot path; set
  it below the shortest idle timeout between you and the database (NAT,
  load balancers and PgBouncer often drop idle connections after a few
  minutes).
- Validation narrows the window but cannot close it: a server can die between
  the ping and the query. Requests that are safe to retry (reads, or writes
  with an idempotency key, problem 001) should retry once on a
  `ConnectionBroken` from a fresh connection.
- Discarding every older connection after one error can reconnect more than
  necessary when only one connection had a problem (for example, one killed
  by `pg_terminate_backend`). Reconnecting 10 connections is cheap compared
  with serving errors.
- With many pods, a short lifetime makes all of them reconnect at about the
  same time. Add jitter to the lifetime, and keep it a little shorter than any
  server-side or proxy connection limit.
- Real pools offer this as configuration: SQLAlchemy `pool_pre_ping` and
  `pool_recycle`, HikariCP `maxLifetime` and `keepaliveTime`, Go
  `database/sql` `SetConnMaxLifetime` and `SetConnMaxIdleTime`. Check what is
  enabled before writing your own.
- For writes after a failover, also make sure new connections reach the new
  primary: use the provider's cluster endpoint or `target_session_attrs=read-write`
  in libpq, and keep DNS TTLs short.

## Run
```bash
python -m unittest -v test_solution.py
```
