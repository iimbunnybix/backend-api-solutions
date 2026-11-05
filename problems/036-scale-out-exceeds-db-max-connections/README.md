# 036 · Autoscaling during a traffic spike made new API instances crash with "too many connections"

## Problem
A ticketing API runs on Kubernetes with a horizontal autoscaler (10 to 40
pods) in front of one PostgreSQL primary with `max_connections = 500`. Each
pod opens a fixed pool of 20 connections at startup and fails its readiness
check if it cannot. On a normal day 10 pods use 200 connections.

When a big concert went on sale, the autoscaler did what it should and scaled
towards 40 pods. The first new pods came up fine. Then every further pod died
at startup with `FATAL: sorry, too many clients already`, went into
CrashLoopBackOff, and the autoscaler kept trying to add more. The pods that
were up were overloaded, so checkout latency climbed while capacity stayed
stuck. The on-call engineer could not open a `psql` session to investigate,
and the scheduled migration job failed for the same reason. Nothing leaked:
every connection was in use by a healthy pool.

## Root cause
**Pool size was set per instance, but the limit is per database.** 20
connections per pod is a local decision. `max_connections` is shared by the
whole fleet, so the real constraint is
`instances × pool_size + other clients ≤ max_connections`. Nobody checked
that against the autoscaler's maximum: 40 × 20 = 800 > 500.

In the reproduction, 10 instances use 200 connections and all start. At 40
instances, **25 start and 15 fail** (500 / 20 = 25), and the database ends
with **0 free connections**, so migrations and admin sessions are locked out.

This is different from problem 005 (a pool *leak*). There each instance lost
connections it never returned. Here every pool works correctly; there are
simply more pools than the database can hold.

## Solution
`pool_size_from_budget` in `solution.py` treats `max_connections` as a budget
for the whole fleet:

1. **Reserve slots first.** Subtract `reserved_connections` (20) for
   migrations, admin shells and monitoring, so they always get in.
2. **Divide by the peak instance count**: the autoscaler maximum *plus* the
   extra pods alive during a rolling deploy (`surge_instances`). Pools are
   sized at startup, so dividing by today's instance count would break as soon
   as the fleet grows.
3. **Round down and cap.** `(500 - 20) // 40 = 12` connections per instance.
   Floor division keeps the total under the limit. The result is capped at
   the desired 20, so a small fleet (`max_instances = 10`, share 48) still
   uses 20 and leaves the rest free.
4. **Fail fast with advice.** Invalid inputs (no instances, negative values,
   a reserve as big as the database) and a share below 2 connections raise
   `PoolBudgetError` at startup with a message saying what to change: raise
   `max_connections`, lower the autoscaler maximum, or add a pooler.

With the fix, all 40 instances start with 12 connections each, 480
connections are in use and exactly 20 stay free. With 10 surge pods the share
is 480 // 50 = 9, and all 50 pods start.

## Trade-offs
- **Smaller pools at peak.** 12 connections per pod instead of 20 means more
  waiting for a connection under load. That is usually better than pods that
  never start, and a database with 500 busy connections is rarely faster than
  one with 480.
- **Sized for the worst case.** The share depends on the *maximum* fleet, so
  at 10 pods only 200 of 480 connections are used. Recomputing pools
  dynamically when the fleet changes is possible but needs coordination
  between instances.
- **Every client must be in the budget**: other services, cron jobs and
  read replicas' own limits. If a second service shares the database, give it
  its own slice of the reserve or of the budget.
- **A connection pooler removes the coupling.** PgBouncer (transaction
  pooling), RDS Proxy or similar lets thousands of client connections share a
  few hundred server connections. At large scale that is the real fix; it
  limits session features (session-level `SET`, advisory locks, some
  prepared-statement modes).
- Raising `max_connections` a lot is not free: each PostgreSQL connection is a
  process with its own memory, and very high counts hurt throughput.

## Run
```bash
python -m unittest -v test_solution.py
```
