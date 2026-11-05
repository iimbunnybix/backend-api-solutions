import unittest

from solution import (BATCH_SIZE, InMemoryBroker, KillSwitch, ProcessKilled, consume,
                      naive_consume, open_order_store, order_message, run_with_restarts,
                      stored_order_ids)

ORDERS = 1000
# Three rolling deploys kill the worker while it saves the 120th, 480th and
# 910th message it handles (counted across restarts).
DEPLOY_KILLS = {120, 480, 910}
OPEN_STORES = []


def tearDownModule():
    """Close every in-memory database the tests opened."""
    for store in OPEN_STORES:
        store.close()


def make_broker(count: int = ORDERS) -> InMemoryBroker:
    """Broker preloaded with ``count`` order messages."""
    broker = InMemoryBroker()
    for number in range(1, count + 1):
        broker.publish(order_message(number))
    return broker


def run(consumer, before_commit=(), after_commit=(), count: int = ORDERS):
    """Process ``count`` orders with the given kill points; return (store, broker, restarts)."""
    broker = make_broker(count)
    store = open_order_store()
    OPEN_STORES.append(store)
    kill = KillSwitch(before_commit=set(before_commit), after_commit=set(after_commit))
    restarts = run_with_restarts(broker, store, consumer, kill)
    return store, broker, restarts


class ReproduceProblem(unittest.TestCase):
    def test_no_orders_are_lost_when_nothing_crashes(self):
        store, _, _ = run(naive_consume)
        self.assertEqual(len(stored_order_ids(store)), ORDERS)

    def test_one_kill_loses_the_rest_of_the_batch(self):
        store, _, _ = run(naive_consume, before_commit={120})
        # Message 120 sits in the batch 101-150, so 120..150 are gone.
        self.assertEqual(ORDERS - len(stored_order_ids(store)), 31)

    def test_three_deploys_lose_orders_silently(self):
        store, broker, restarts = run(naive_consume, before_commit=DEPLOY_KILLS)
        self.assertEqual(restarts, 3)
        self.assertEqual(ORDERS - len(stored_order_ids(store)), 93)
        self.assertTrue(broker.is_drained())        # the queue looks perfectly healthy
        self.assertEqual(broker.redeliveries, 0)    # nothing was ever retried


class FixAckAfterCommit(unittest.TestCase):
    def test_three_deploys_lose_no_orders(self):
        store, broker, restarts = run(consume, before_commit=DEPLOY_KILLS)
        self.assertEqual(restarts, 3)
        self.assertEqual(len(stored_order_ids(store)), ORDERS)
        self.assertTrue(broker.is_drained())

    def test_kill_redelivers_only_the_unacked_part_of_the_batch(self):
        _, broker, _ = run(consume, before_commit={120})
        self.assertEqual(broker.redeliveries, 31)

    def test_kill_after_commit_does_not_duplicate_the_order(self):
        store, broker, _ = run(consume, after_commit={120})
        row_count = store.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
        self.assertEqual(row_count, ORDERS)
        self.assertEqual(broker.redeliveries, 31)   # 120 itself comes back, already saved

    def test_kill_on_the_last_message_of_a_batch(self):
        store, _, _ = run(consume, before_commit={BATCH_SIZE})
        self.assertEqual(len(stored_order_ids(store)), ORDERS)

    def test_repeated_kills_on_the_same_message_still_finish(self):
        store, _, restarts = run(consume, before_commit={5, 6, 7}, count=10)
        # Each restart re-handles message 5 first, so the counter keeps moving.
        self.assertEqual(restarts, 3)
        self.assertEqual(len(stored_order_ids(store)), 10)

    def test_redelivery_keeps_original_order(self):
        broker = make_broker(5)
        broker.receive(3, ack_on_receive=False)
        broker.connection_lost()
        batch = broker.receive(5, ack_on_receive=False)
        self.assertEqual([delivery.tag for delivery in batch], [1, 2, 3, 4, 5])
        self.assertTrue(all(delivery.redelivered for delivery in batch[:3]))
        self.assertFalse(batch[3].redelivered)

    def test_unacked_message_is_not_drained(self):
        broker = make_broker(1)
        broker.receive(1, ack_on_receive=False)
        self.assertFalse(broker.is_drained())

    def test_kill_is_a_base_exception_so_handlers_cannot_swallow_it(self):
        self.assertFalse(issubclass(ProcessKilled, Exception))


if __name__ == "__main__":
    unittest.main()
