import unittest

from solution import (CHECKOUT, QUERY, RELEASE, ConnectionPool, NestedCheckoutError,
                      naive_place_order_steps, place_order_steps, run_burst)

POOL_SIZE = 10


def make_pool(strict: bool = False) -> ConnectionPool:
    return ConnectionPool(POOL_SIZE, strict=strict)


class ReproduceProblem(unittest.TestCase):
    def test_naive_handler_works_at_low_concurrency(self):
        result = run_burst(naive_place_order_steps(), 5, make_pool())
        self.assertEqual((result.completed, result.stuck), (5, 0))

    def test_naive_handler_still_works_one_below_pool_size(self):
        result = run_burst(naive_place_order_steps(), POOL_SIZE - 1, make_pool())
        self.assertEqual(result.stuck, 0)

    def test_burst_equal_to_pool_size_deadlocks_every_request(self):
        result = run_burst(naive_place_order_steps(), POOL_SIZE, make_pool())
        self.assertEqual((result.completed, result.stuck), (0, POOL_SIZE))

    def test_deadlocked_requests_each_hold_one_connection_and_wait_for_another(self):
        result = run_burst(naive_place_order_steps(), POOL_SIZE, make_pool())
        self.assertEqual(len(result.stuck_holding), POOL_SIZE)
        self.assertEqual(set(result.stuck_holding.values()), {1})

    def test_larger_burst_also_stops_everything(self):
        result = run_burst(naive_place_order_steps(), 3 * POOL_SIZE, make_pool())
        self.assertEqual(result.completed, 0)


class FixWithOneConnectionPerRequest(unittest.TestCase):
    def test_burst_equal_to_pool_size_completes(self):
        result = run_burst(place_order_steps(), POOL_SIZE, make_pool())
        self.assertEqual((result.completed, result.stuck), (POOL_SIZE, 0))

    def test_burst_larger_than_pool_completes_by_queueing(self):
        result = run_burst(place_order_steps(), 3 * POOL_SIZE, make_pool())
        self.assertEqual(result.completed, 3 * POOL_SIZE)

    def test_all_connections_are_returned_afterwards(self):
        pool = make_pool()
        run_burst(place_order_steps(), 3 * POOL_SIZE, pool)
        self.assertEqual(pool.in_use(), 0)

    def test_fixed_handler_never_holds_two_connections(self):
        self.assertEqual(place_order_steps().count(CHECKOUT), 1)


class FixWithStrictPool(unittest.TestCase):
    def test_strict_pool_catches_nested_checkout_with_a_single_request(self):
        with self.assertRaises(NestedCheckoutError):
            run_burst(naive_place_order_steps(), 1, make_pool(strict=True))

    def test_strict_pool_accepts_the_fixed_handler(self):
        result = run_burst(place_order_steps(), 3 * POOL_SIZE, make_pool(strict=True))
        self.assertEqual(result.completed, 3 * POOL_SIZE)

    def test_strict_pool_allows_checkout_again_after_release(self):
        steps = [CHECKOUT, QUERY, RELEASE, CHECKOUT, QUERY, RELEASE]
        result = run_burst(steps, 1, make_pool(strict=True))
        self.assertEqual(result.completed, 1)


class PoolBasics(unittest.TestCase):
    def test_checkout_waits_when_pool_is_empty(self):
        pool = ConnectionPool(1)
        self.assertTrue(pool.try_checkout("a"))
        self.assertFalse(pool.try_checkout("b"))

    def test_releasing_a_connection_not_held_is_an_error(self):
        with self.assertRaises(ValueError):
            make_pool().release("nobody")

    def test_pool_size_below_one_is_rejected(self):
        with self.assertRaises(ValueError):
            ConnectionPool(0)

    def test_release_step_without_checkout_is_reported(self):
        with self.assertRaises(ValueError):
            run_burst([RELEASE], 1, make_pool())


if __name__ == "__main__":
    unittest.main()
