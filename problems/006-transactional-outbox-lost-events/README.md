# 006 · Customers were charged but their orders never shipped, and nothing in the logs said why

## Problem
The order service saves each order to PostgreSQL and then publishes an
`OrderPlaced` event to the message broker; the warehouse, email and loyalty
services all consume it. During a 15-minute broker maintenance window, and
again during a rolling deploy, a few hundred orders were committed and paid
for but never reached the warehouse. Support found them only when customers
complained a week later. Reconciling them took a manual SQL diff between the
orders table and the warehouse's records.

## Root cause
A **dual write**: two systems (database and broker) updated one after the
other with no shared transaction.

- *Commit, then publish:* if the broker call fails, or the pod is killed
  between `COMMIT` and `publish()`, the order exists but the event is lost
  forever. The test reproduces this with a single broker failure.
- *Publish, then commit* just flips the bug: a failed commit leaves an event
  for an order that does not exist.

No retry inside the handler fixes this, because the process itself may be gone.

## Solution
**Transactional outbox.**

1. `place_order` inserts the order **and** an `outbox` row (event id, topic,
   payload) in one database transaction. Both commit or neither does; the test
   kills the "process" before commit and finds no order and no event.
2. `OutboxRelay.run_once()` reads unsent rows in `seq` order, publishes each,
   and marks it sent only after the broker accepts it. On failure it stops, so
   a later event never overtakes an earlier one. In the test, 5 orders survive
   3 failed relay runs and are then delivered in their original order.
3. Delivery is **at-least-once**: if the relay crashes after publishing but
   before marking the row, it re-sends. Every event has a stable `event_id`,
   and `DedupingConsumer` processes each id once.
4. `backlog()` is the health signal (alert if it grows), and `purge_sent()`
   keeps the table small.

Because the outbox lives in the database, it survives restarts: the test
writes an order, "kills" the process, reopens the DB file and still delivers it.

## Trade-offs
- Adds latency (one relay polling interval) and a table that must be purged.
  Change-data-capture (e.g. Debezium on the WAL) can replace polling and reads
  the outbox with lower latency and less DB load.
- Consumers **must** be idempotent. Store processed event ids in the same
  transaction as the consumer's side effect, otherwise dedup has its own
  dual-write gap.
- Strict ordering needs one active relay per outbox (or per partition key);
  several concurrent relays need `SELECT ... FOR UPDATE SKIP LOCKED` and then
  only guarantee ordering per key.
- A permanently bad event blocks the queue behind it ("stop at first
  failure"). Add a max-attempts threshold that moves it to a dead-letter table
  and pages someone.

## Run
```bash
python -m unittest -v test_solution.py
```
