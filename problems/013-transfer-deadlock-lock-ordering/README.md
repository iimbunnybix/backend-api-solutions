# 013 · Wallet transfers failed with "deadlock detected" every evening, and the support queue filled with "my money is stuck" tickets

## Problem
A peer-to-peer wallet service moves money between user balances in
PostgreSQL. Each transfer runs in one transaction: `SELECT ... FOR UPDATE` the
sender's row, then the recipient's row, check the balance, write both. During
the evening peak, groups of friends split bills and pay each other back within
seconds (A pays B while B pays A), and a small number of merchant accounts
receive and send hundreds of transfers a minute.

Every evening the API returned bursts of 500s with `ERROR: deadlock detected`
from the database. The mobile app showed "transfer failed", users retried or
gave up, and support received tickets from people who thought their money was
lost. Each deadlock also held both rows locked until the database's deadlock
detector ran (PostgreSQL's `deadlock_timeout` defaults to 1 second), so other
transfers touching those accounts queued up behind them.

## Root cause
**Inconsistent lock ordering.** The code locked rows in the order it read
them: sender first, then recipient. Two opposite transfers between the same
pair of accounts each lock their own sender and then wait for the other's:

```
T1 (A -> B): lock A ............ wait for B
T2 (B -> A):        lock B ..... wait for A      <- cycle: database aborts one
```

Longer cycles (A->B, B->C, C->A) cause the same thing. The database is doing
its job by breaking the cycle; the application turned the abort into a user
facing error because it did not retry.

In the deterministic simulation in the tests (20 seeded runs of 400 random
transfers among 10 busy accounts, 8 transfers in flight), **137 of 8,000
transfers (1.7%)** were aborted as deadlock victims with the original code.

## Solution
`solution.py` contains a small model of a row-lock manager (exclusive locks,
wait-for graph, youngest-transaction-in-cycle is the victim) and a round-robin
scheduler, so deadlocks can be reproduced step by step instead of relying on
thread timing.

1. **Lock in a global order.** `ordered_transfer` locks both accounts in
   ascending id order (in SQL: `SELECT ... WHERE id IN (:a, :b) ORDER BY id
   FOR UPDATE`). Every transaction acquires locks in the same order, so a wait
   cycle cannot form. `lock_in_order` dedupes and sorts any set of keys for
   multi-row operations (batch payouts, bulk updates).
2. **Retry deadlock victims.** Ordering removes deadlocks between the code
   paths you control, but other jobs, triggers or foreign-key checks can still
   take locks in a different order. The scheduler's `max_retries` models a
   retry wrapper that re-runs the whole transaction on a deadlock (SQLSTATE
   `40P01`) or serialization failure (`40001`). A retried transaction keeps
   its original start order so it is not chosen as the victim every time.
3. **Business failures are not retried.** Insufficient funds aborts once.

Results from the tests: with ordered locking, 0 deadlocks in all 20 seeded
workloads (also at 16 in flight), every transfer commits, total money is
conserved, and the final balances equal a serial execution. Retry alone on the
naive code makes every request succeed but the deadlocks (and their lock-wait
delays) still happen: 14 on seed 1.

## Trade-offs
- Ordering needs discipline: every code path that locks more than one row of
  the same table (and across tables: always lock `accounts` before
  `ledger_entries`, for example) must follow it. Put it in one helper and
  review for raw `FOR UPDATE` queries.
- Retries must re-run the whole transaction, not just the failing statement,
  and must be safe to repeat (combine with idempotency keys, see 001).
- A single very hot account still serialises all its transfers. If that is
  the bottleneck, use an append-only ledger with asynchronous balance
  aggregation or split the hot balance into sub-accounts.
- Alternatives: `SERIALIZABLE` isolation plus retries (simpler code, more
  aborts under contention), or a single atomic statement
  (`UPDATE ... SET balance = balance - :x WHERE id = :a AND balance >= :x`)
  when the logic is simple enough.
- `NOWAIT` / `lock_timeout` turn long waits into fast failures, which helps
  latency but still needs a retry.

## Run
```bash
python -m unittest -v test_solution.py
```
