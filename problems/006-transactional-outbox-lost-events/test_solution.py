import json
import os
import tempfile
import unittest

from solution import (DedupingConsumer, OutboxRelay, connect, place_order,
                      place_order_dual_write)


class FakeBroker:
    """Delivers to a consumer; can be told to fail the next N publishes."""

    def __init__(self, consumer=None):
        self.consumer = consumer or DedupingConsumer()
        self.fail_next = 0
        self.delivered = []

    def publish(self, topic, event_id, payload):
        if self.fail_next:
            self.fail_next -= 1
            raise ConnectionError("broker unavailable")
        self.delivered.append(event_id)
        self.consumer.handle(event_id, payload)


def order_count(db):
    return db.execute("SELECT COUNT(*) FROM orders").fetchone()[0]


class ReproduceDualWrite(unittest.TestCase):
    def test_broker_failure_after_commit_loses_event(self):
        db, broker = connect(), FakeBroker()
        broker.fail_next = 1
        with self.assertRaises(ConnectionError):
            place_order_dual_write(db, broker, "alice", 1999)
        self.assertEqual(order_count(db), 1)                # customer was charged
        self.assertEqual(broker.consumer.shipped, [])       # warehouse never hears


class Outbox(unittest.TestCase):
    def setUp(self):
        self.db, self.broker = connect(), FakeBroker()
        self.relay = OutboxRelay(self.db, self.broker)

    def test_order_and_event_commit_together(self):
        oid = place_order(self.db, "alice", 1999)
        self.assertEqual(self.relay.backlog(), 1)
        self.assertEqual(self.relay.run_once(), 1)
        self.assertEqual(self.broker.consumer.shipped, [oid])

    def test_crash_before_commit_leaves_neither(self):
        def crash():
            raise SystemExit("pod killed")
        with self.assertRaises(SystemExit):
            place_order(self.db, "alice", 1999, before_commit=crash)
        self.assertEqual(order_count(self.db), 0)
        self.assertEqual(self.relay.backlog(), 0)

    def test_validation_failure_publishes_nothing(self):
        with self.assertRaises(ValueError):
            place_order(self.db, "alice", 0)
        self.assertEqual(self.relay.run_once(), 0)

    def test_broker_outage_is_retried_without_loss_and_in_order(self):
        ids = [place_order(self.db, f"c{i}", 100 + i) for i in range(5)]
        self.broker.fail_next = 3
        for _ in range(3):
            self.assertEqual(self.relay.run_once(), 0)   # stops at first failure
        self.assertEqual(self.relay.backlog(), 5)
        self.assertEqual(self.relay.run_once(), 5)
        self.assertEqual(self.broker.consumer.shipped, ids)
        attempts = self.db.execute("SELECT attempts FROM outbox ORDER BY seq").fetchall()
        self.assertEqual([a for (a,) in attempts], [4, 1, 1, 1, 1])

    def test_relay_crash_after_publish_redelivers_but_consumer_dedups(self):
        oid = place_order(self.db, "alice", 1999)
        row = self.db.execute("SELECT event_id, payload FROM outbox").fetchone()
        # Relay published but died before marking sent:
        self.broker.publish("orders", row[0], json.loads(row[1]))
        self.relay.run_once()                       # publishes again
        self.assertEqual(len(self.broker.delivered), 2)
        self.assertEqual(self.broker.consumer.shipped, [oid])

    def test_failed_processing_does_not_swallow_redelivery(self):
        consumer = DedupingConsumer()
        # First attempt fails inside the side effect (malformed payload).
        with self.assertRaises(KeyError):
            consumer.handle("e1", {"orderId": "o1"})
        # The broker redelivers; it must be processed, exactly once.
        self.assertTrue(consumer.handle("e1", {"order_id": "o1"}))
        self.assertFalse(consumer.handle("e1", {"order_id": "o1"}))
        self.assertEqual(consumer.shipped, ["o1"])

    def test_batch_size_and_purge(self):
        for i in range(250):
            place_order(self.db, "bulk", 100)
        relay = OutboxRelay(self.db, self.broker, batch_size=100)
        self.assertEqual([relay.run_once() for _ in range(4)], [100, 100, 50, 0])
        self.assertEqual(relay.purge_sent(), 250)
        self.assertEqual(relay.backlog(), 0)

    def test_survives_process_restart_with_file_db(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "orders.db")
            db = connect(path)
            oid = place_order(db, "alice", 1999)
            db.close()                                # process dies before relay ran
            db2 = connect(path)
            broker = FakeBroker()
            self.assertEqual(OutboxRelay(db2, broker).run_once(), 1)
            self.assertEqual(broker.consumer.shipped, [oid])
            db2.close()


if __name__ == "__main__":
    unittest.main()
