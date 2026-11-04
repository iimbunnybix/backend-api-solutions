import json
import unittest

from solution import (FakeClock, InMemoryQueue, ResilientConsumer, TransientError,
                      handle_order_event, naive_consume, replay_dead_letters)


def build_queue(clock, poison_at=0, good=1000):
    q = InMemoryQueue(clock)
    for i in range(good + 1):
        if i == poison_at:
            q.publish("poison", '{"order_id": 42,')      # truncated JSON
        else:
            q.publish(f"m{i}", json.dumps({"order_id": i}))
    return q


class ReproducePoisonLoop(unittest.TestCase):
    def test_one_bad_message_blocks_the_whole_queue(self):
        clock = FakeClock()
        q = build_queue(clock)
        processed = []
        acked = naive_consume(q, lambda b: handle_order_event(b, processed), max_receives=50_000)
        self.assertEqual(acked, 0)                  # 50,000 receives, zero progress
        self.assertEqual(processed, [])
        self.assertEqual(len(q), 1001)

    def test_messages_ahead_of_poison_get_through_then_stall(self):
        clock = FakeClock()
        q = build_queue(clock, poison_at=300)
        processed = []
        naive_consume(q, lambda b: handle_order_event(b, processed), max_receives=50_000)
        self.assertEqual(len(processed), 300)


class DeadLetterFix(unittest.TestCase):
    def test_permanent_error_goes_straight_to_dlq(self):
        clock = FakeClock()
        q = build_queue(clock)
        processed = []
        c = ResilientConsumer(q, lambda b: handle_order_event(b, processed))
        c.drain(clock)
        self.assertEqual(len(processed), 1000)
        self.assertEqual(c.acked, 1000)
        self.assertEqual(len(c.dead_letters), 1)
        letter = c.dead_letters[0]
        self.assertEqual(letter.message.id, "poison")
        self.assertEqual(letter.message.deliveries, 1)     # no pointless retries
        self.assertEqual(letter.error_type, "PermanentError")
        self.assertIn("invalid JSON", letter.reason)
        self.assertEqual(len(q), 0)
        self.assertEqual(clock.now(), 0.0)                 # never waited on backoff

    def test_transient_error_retries_with_backoff_then_succeeds(self):
        clock = FakeClock()
        q = InMemoryQueue(clock)
        q.publish("a", "{}")
        failures = {"left": 3}

        def flaky(_body):
            if failures["left"]:
                failures["left"] -= 1
                raise TransientError("db timeout")

        c = ResilientConsumer(q, flaky, base_delay=1.0)
        c.drain(clock)
        self.assertEqual(c.acked, 1)
        self.assertEqual(c.dead_letters, [])
        self.assertEqual(clock.now(), 1 + 2 + 4)            # exponential backoff

    def test_always_failing_message_is_bounded(self):
        clock = FakeClock()
        q = InMemoryQueue(clock)
        q.publish("bad", "{}")
        q.publish("good", "{}")
        attempts = []

        def handler(body):
            attempts.append(body)
            raise TransientError("still down")

        c = ResilientConsumer(q, handler, max_attempts=5, base_delay=1.0)
        c.drain(clock)
        self.assertEqual(len(attempts), 10)                 # 5 per message, then stop
        self.assertEqual(len(c.dead_letters), 2)
        self.assertTrue(all(d.message.deliveries == 5 for d in c.dead_letters))
        self.assertEqual(len(q), 0)

    def test_retrying_message_does_not_block_others(self):
        clock = FakeClock()
        q = InMemoryQueue(clock)
        q.publish("slow", "slow")
        for i in range(5):
            q.publish(f"m{i}", "ok")
        order = []

        def handler(body):
            if body == "slow" and order.count("slow-fail") < 1:
                order.append("slow-fail")
                raise TransientError("lock wait")
            order.append(body)

        c = ResilientConsumer(q, handler)
        c.drain(clock)
        self.assertEqual(order, ["slow-fail", "ok", "ok", "ok", "ok", "ok", "slow"])

    def test_backoff_is_capped(self):
        c = ResilientConsumer(InMemoryQueue(FakeClock()), lambda b: None,
                              base_delay=1.0, max_delay=10.0)
        self.assertEqual([c.backoff(a) for a in range(1, 7)], [1, 2, 4, 8, 10, 10])

    def test_replay_after_fix(self):
        clock = FakeClock()
        q = build_queue(clock, good=3)
        processed = []
        c = ResilientConsumer(q, lambda b: handle_order_event(b, processed))
        c.drain(clock)
        n = replay_dead_letters(c.dead_letters, q,
                                lambda body: body + ' "x": 1}' if body.endswith(",") else None)
        self.assertEqual(n, 1)
        self.assertEqual(c.dead_letters, [])
        c.drain(clock)
        self.assertIn(42, processed)

    def test_missing_field_is_permanent(self):
        with self.assertRaisesRegex(Exception, "missing order_id"):
            handle_order_event('{"id": 1}', [])


if __name__ == "__main__":
    unittest.main()
