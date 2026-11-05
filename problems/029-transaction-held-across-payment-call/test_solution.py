import unittest

from solution import (DB_WORK_SECONDS, STATUS_FAILED, STATUS_PAID, STATUS_PENDING, ConnectionPool,
                      FakeClock, FakePaymentProvider, PaymentDeclined, PoolExhausted,
                      connections_needed, create_database, find_stuck_pending_orders,
                      naive_place_order, order_status, place_order)

NORMAL_LATENCY = 0.3
DEGRADED_LATENCY = 3.0
PEAK_CHECKOUTS_PER_SECOND = 20
POOL_SIZE = 10
AMOUNT_CENTS = 4999


def make_system(latency=DEGRADED_LATENCY, pool_size=POOL_SIZE):
    clock = FakeClock()
    conn = create_database()
    pool = ConnectionPool(conn, clock, pool_size)
    provider = FakePaymentProvider(clock, pool, latency)
    return clock, conn, pool, provider


def process_dies():
    """Simulate the API pod being killed while the provider call is in flight."""
    raise SystemExit("pod killed")


class ReproduceProblem(unittest.TestCase):
    def test_connection_is_held_for_the_whole_provider_call(self):
        clock, _, pool, provider = make_system()
        naive_place_order(pool, provider, clock, "order-1", AMOUNT_CENTS)
        self.assertEqual(provider.connections_held_during_calls, [1])
        self.assertAlmostEqual(pool.hold_times[0], DEGRADED_LATENCY + DB_WORK_SECONDS)

    def test_transaction_and_write_lock_stay_open_during_the_call(self):
        clock, conn, pool, provider = make_system()
        seen = []
        provider.during_charge = lambda: seen.append(conn.in_transaction)
        naive_place_order(pool, provider, clock, "order-1", AMOUNT_CENTS)
        self.assertEqual(seen, [True])

    def test_concurrent_checkout_finds_pool_exhausted(self):
        clock, _, pool, provider = make_system(pool_size=1)
        errors = []

        def second_customer():
            try:
                naive_place_order(pool, provider, clock, "order-2", AMOUNT_CENTS)
            except PoolExhausted as exc:
                errors.append(exc)

        provider.during_charge = second_customer
        naive_place_order(pool, provider, clock, "order-1", AMOUNT_CENTS)
        self.assertEqual(len(errors), 1)

    def test_slow_provider_needs_more_connections_than_the_pool_has(self):
        clock, _, pool, provider = make_system()
        naive_place_order(pool, provider, clock, "order-1", AMOUNT_CENTS)
        needed = connections_needed(PEAK_CHECKOUTS_PER_SECOND, max(pool.hold_times))
        self.assertGreater(needed, POOL_SIZE)


class FixShortTransactionsAroundTheCall(unittest.TestCase):
    def test_no_connection_is_held_during_the_provider_call(self):
        clock, _, pool, provider = make_system()
        place_order(pool, provider, clock, "order-1", AMOUNT_CENTS)
        self.assertEqual(provider.connections_held_during_calls, [0])

    def test_each_transaction_holds_a_connection_only_for_db_work(self):
        clock, _, pool, provider = make_system()
        place_order(pool, provider, clock, "order-1", AMOUNT_CENTS)
        self.assertEqual(len(pool.hold_times), 2)
        for hold in pool.hold_times:
            self.assertAlmostEqual(hold, DB_WORK_SECONDS)

    def test_concurrent_checkout_succeeds_with_a_single_connection(self):
        clock, _, pool, provider = make_system(pool_size=1)
        results = []
        provider.during_charge = lambda: results.append(
            place_order(pool, provider, clock, "order-2", AMOUNT_CENTS))
        place_order(pool, provider, clock, "order-1", AMOUNT_CENTS)
        self.assertEqual(results, [STATUS_PAID])
        self.assertEqual(order_status(pool, "order-1"), STATUS_PAID)

    def test_order_is_committed_as_pending_before_money_moves(self):
        clock, _, pool, provider = make_system()
        seen = []
        provider.during_charge = lambda: seen.append(order_status(pool, "order-1"))
        place_order(pool, provider, clock, "order-1", AMOUNT_CENTS)
        self.assertEqual(seen, [STATUS_PENDING])

    def test_declined_payment_marks_order_failed(self):
        clock, _, pool, provider = make_system()
        provider.decline = True
        self.assertEqual(place_order(pool, provider, clock, "order-1", AMOUNT_CENTS), STATUS_FAILED)
        self.assertEqual(order_status(pool, "order-1"), STATUS_FAILED)

    def test_order_left_pending_by_a_crash_is_found_by_the_reconciler(self):
        clock, _, pool, provider = make_system()
        provider.during_charge = process_dies
        with self.assertRaises(SystemExit):
            place_order(pool, provider, clock, "order-1", AMOUNT_CENTS)
        clock.advance(60)
        self.assertEqual(find_stuck_pending_orders(pool, clock, older_than_seconds=30), ["order-1"])

    def test_fixed_handler_needs_far_fewer_connections_at_peak(self):
        clock, _, pool, provider = make_system()
        place_order(pool, provider, clock, "order-1", AMOUNT_CENTS)
        needed = connections_needed(PEAK_CHECKOUTS_PER_SECOND, sum(pool.hold_times))
        self.assertLess(needed, 1)


class ReadmeNumbers(unittest.TestCase):
    def test_capacity_figures(self):
        self.assertAlmostEqual(connections_needed(PEAK_CHECKOUTS_PER_SECOND, NORMAL_LATENCY + DB_WORK_SECONDS), 6.1)
        self.assertAlmostEqual(connections_needed(PEAK_CHECKOUTS_PER_SECOND, DEGRADED_LATENCY + DB_WORK_SECONDS), 60.1)
        self.assertAlmostEqual(connections_needed(PEAK_CHECKOUTS_PER_SECOND, 2 * DB_WORK_SECONDS), 0.2)


if __name__ == "__main__":
    unittest.main()
