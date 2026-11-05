# 034 · Paid orders vanished during deploys, with no error in any log and an empty queue

## Problem
An online store's checkout publishes an `order.paid` message for each paid
order; a fulfilment worker consumes them and writes them into the
warehouse database so they can be picked and shipped. Customers started
writing in a week or two after paying: "I was charged but my order never
arrived". Each time, the payment existed, but the order was missing from the
warehouse database. There was no exception in the worker logs, no message in
any dead-letter queue, and the queue-depth dashboard showed zero backlog. The
missing orders clustered around deploy times, and finance had to refund or
re-ship them by hand.

## Root cause
**The worker acknowledged messages before it processed them.** It fetched
messages in batches of 50 and told the broker "done" for the whole batch the
moment it received it, then saved the orders one by one. Acknowledging is how
the broker learns it may delete a message. From that point the batch existed
only in the worker's memory.

A rolling deploy sends `SIGTERM`, then `SIGKILL` after the grace period; the
OOM killer skips straight to `SIGKILL`. Either way, the process stopped mid-batch
and every order not yet saved in that batch was gone. The broker had nothing
to redeliver, so nothing retried, nothing errored, and the queue looked
healthy.

In the reproduction (1,000 orders, batches of 50), one kill while saving
message 120 loses messages 120 to 150, so **31 orders**. Three kills (one per
deploy, at the 120th, 480th and 910th message handled) lose **93 orders**,
the broker reports **0 redeliveries** and the queue ends fully drained.

## Solution
`consume` in `solution.py`:

1. **Receive without acking.** Messages stay owned by the broker as *unacked*.
   If the worker's connection drops, the broker puts them back at the front of
   the queue for the next worker (`connection_lost`).
2. **Ack after commit.** Each order is saved in its own transaction, and only
   after the commit does the worker ack that one message. A kill before the
   commit means the message comes back.
3. **Idempotent write.** A kill *after* the commit but *before* the ack
   also brings the message back, so the order would be saved twice. The insert
   uses `ON CONFLICT (order_id) DO NOTHING`, which makes a redelivered message
   a no-op.

With the same three kills, all 1,000 orders are stored, 93 messages are
redelivered (the unacked rest of each interrupted batch), and the queue drains.
A kill between commit and ack still leaves exactly 1,000 rows.

## Trade-offs
- **At-least-once, not exactly-once.** Ack-after-commit means duplicates on
  redelivery; correctness depends on the idempotent write. Side effects that
  are not database writes (emails, payment calls) need their own idempotency
  key or an outbox.
- **Redelivered work.** After a kill, the rest of the batch is processed again.
  Smaller batches (lower prefetch) waste less on a crash but cost more round
  trips per message.
- The same bug hides behind library defaults: RabbitMQ consumers with
  `autoAck=true`, SQS workers that delete messages right after
  `ReceiveMessage`, and Kafka consumers with `enable.auto.commit=true`, which
  commits offsets on a timer whether or not the records were processed.
- **Graceful shutdown helps but is not enough.** Handling `SIGTERM` by finishing
  the current message and then stopping reduces redeliveries, but `SIGKILL` and
  OOM kills cannot be caught, so the ack order is what keeps data safe.
- Acking one message at a time is slower than batch acks. Brokers allow
  acknowledging several at once ("multiple" acks, batch deletes); that is fine as
  long as only already-committed messages are included.

## Run
```bash
python -m unittest -v test_solution.py
```
