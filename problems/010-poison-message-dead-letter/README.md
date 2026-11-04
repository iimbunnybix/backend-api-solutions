# 010 · Order confirmation emails stopped for six hours while the queue consumer ran at 100% CPU

## Problem
The order service publishes an `order.created` event to a queue; a consumer
sends the confirmation email and updates the CRM. One afternoon a partner
integration published a truncated event (`{"order_id": 42,`). From then on no
confirmation emails went out. Consumer CPU was pegged, logs filled with the
same `JSONDecodeError` thousands of times per minute, and the backlog grew
until someone found the bad message and deleted it by hand. Customers who had
not received a confirmation placed duplicate orders and opened support tickets.

## Root cause
**The consumer treated every failure as retryable and retried forever, at the
head of the queue.** On any exception it called `nack(requeue=True)`, which
puts the message straight back at the front. A message that can *never* be
parsed was therefore redelivered immediately, failed again, and blocked every
valid message behind it (a "poison message").

In the reproduction, a queue with the poison message first and 1,000 valid
messages behind it gets **0 messages processed in 50,000 receives**. If the
poison message sits at position 300, exactly the 300 messages ahead of it get
through and then processing stops.

## Solution
`ResilientConsumer` in `solution.py`:

1. **Classify errors.** Bad input (invalid JSON, missing `order_id`) raises
   `PermanentError` and goes to the **dead-letter queue (DLQ)** on the first
   delivery, with the error type and reason attached. Retrying it would only
   waste work.
2. **Bounded retries for everything else.** Unknown and transient errors are
   retried with exponential backoff (1 s, 2 s, 4 s … capped at 60 s), up to
   `max_attempts` deliveries (default 5), then dead-lettered.
3. **Delayed retries go to the back.** A message waiting for its backoff is
   invisible, so the messages behind it keep flowing.
4. **Ack only after the dead letter is stored**, so a crash cannot lose it.
5. **Replay tooling.** `replay_dead_letters` republishes messages after a fix,
   so a DLQ becomes a work queue for humans rather than a graveyard.

With the fix, all 1,000 valid messages are processed, the poison message is
in the DLQ after a single delivery, and no time is spent on backoff. A message
that fails transiently three times succeeds on the fourth delivery after
1 + 2 + 4 = 7 s of backoff.

## Trade-offs
- **Ordering:** moving a failing message aside lets later messages overtake
  it. If per-key order matters (e.g. `order.updated` after `order.created`),
  park the whole key, or make the handler tolerate out-of-order events.
- **Classification is a judgment call.** Misclassifying a transient error as
  permanent sends good messages to the DLQ; the reverse costs a few wasted
  retries. When unsure, treat it as transient with a bounded retry count.
- The DLQ needs an **alert on depth/age** and an owner; otherwise problems are
  only hidden, not fixed.
- Most brokers have this built in (SQS redrive policy with `maxReceiveCount`,
  RabbitMQ dead-letter exchanges, Kafka consumers writing to a DLT). Prefer
  that over hand-rolled logic; the handler still has to decide what is
  permanent.
- Validate events at the **producer** too (schema registry or contract tests)
  so fewer poison messages are created in the first place.

## Run
```bash
python -m unittest -v test_solution.py
```
