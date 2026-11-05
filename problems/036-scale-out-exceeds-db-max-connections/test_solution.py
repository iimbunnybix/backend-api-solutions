import unittest

from solution import (DB_MAX_CONNECTIONS, MIN_USEFUL_POOL_SIZE, RESERVED_CONNECTIONS,
                      FakeDatabase, PoolBudget, PoolBudgetError, free_connections, naive_pool_size,
                      pool_size_from_budget, scale_out)

NORMAL_FLEET = 10
PEAK_FLEET = 40


def run_fleet(instance_count: int, pool_size: int):
    """Start a fleet against a fresh database; return (result, db)."""
    db = FakeDatabase(DB_MAX_CONNECTIONS)
    return scale_out(db, instance_count, pool_size), db


class ReproduceProblem(unittest.TestCase):
    def test_fixed_pools_fit_at_normal_fleet_size(self):
        result, _ = run_fleet(NORMAL_FLEET, naive_pool_size(PoolBudget()))
        self.assertEqual(result.failed_instances, 0)
        self.assertEqual(result.connections_in_use, 200)

    def test_scaling_to_40_instances_exceeds_max_connections(self):
        result, _ = run_fleet(PEAK_FLEET, naive_pool_size(PoolBudget()))
        # 500 / 20 = 25 instances fit; the other 15 fail with "too many connections".
        self.assertEqual(result.healthy_instances, 25)
        self.assertEqual(result.failed_instances, 15)

    def test_migrations_cannot_connect_after_scale_out(self):
        _, db = run_fleet(PEAK_FLEET, naive_pool_size(PoolBudget()))
        self.assertEqual(free_connections(db), 0)


class FixPoolSizeFromBudget(unittest.TestCase):
    def test_pool_size_is_the_fair_share_of_the_budget(self):
        # (500 - 20) // 40 = 12
        self.assertEqual(pool_size_from_budget(PoolBudget(max_instances=PEAK_FLEET)), 12)

    def test_all_40_instances_start(self):
        size = pool_size_from_budget(PoolBudget(max_instances=PEAK_FLEET))
        result, _ = run_fleet(PEAK_FLEET, size)
        self.assertEqual(result.healthy_instances, PEAK_FLEET)
        self.assertEqual(result.failed_instances, 0)
        self.assertEqual(result.connections_in_use, 480)

    def test_reserved_slots_stay_free_for_migrations(self):
        size = pool_size_from_budget(PoolBudget(max_instances=PEAK_FLEET))
        _, db = run_fleet(PEAK_FLEET, size)
        self.assertEqual(free_connections(db), RESERVED_CONNECTIONS)

    def test_small_fleet_gets_desired_size_not_more(self):
        # (500 - 20) // 10 = 48, capped at the desired 20.
        self.assertEqual(pool_size_from_budget(PoolBudget(max_instances=NORMAL_FLEET)), 20)

    def test_deploy_surge_is_counted_in_the_divisor(self):
        budget = PoolBudget(max_instances=PEAK_FLEET, surge_instances=10)
        size = pool_size_from_budget(budget)
        self.assertEqual(size, 9)                       # 480 // 50
        result, _ = run_fleet(PEAK_FLEET + 10, size)
        self.assertEqual(result.failed_instances, 0)

    def test_uneven_division_rounds_down_and_stays_under_limit(self):
        budget = PoolBudget(db_max_connections=100, reserved_connections=3, max_instances=7)
        size = pool_size_from_budget(budget)
        self.assertEqual(size, 13)                      # 97 // 7
        self.assertLessEqual(size * 7, 97)


class FixValidation(unittest.TestCase):
    def assert_rejected(self, message_part: str, **overrides):
        """pool_size_from_budget must raise PoolBudgetError mentioning ``message_part``."""
        with self.assertRaises(PoolBudgetError) as caught:
            pool_size_from_budget(PoolBudget(**overrides))
        self.assertIn(message_part, str(caught.exception))

    def test_too_many_instances_for_the_budget_is_rejected_with_advice(self):
        # 480 // 300 = 1, below the minimum of 2.
        self.assert_rejected("PgBouncer", max_instances=300)

    def test_share_equal_to_minimum_is_accepted(self):
        self.assertEqual(pool_size_from_budget(PoolBudget(max_instances=240)),
                         MIN_USEFUL_POOL_SIZE)

    def test_zero_instances_is_rejected(self):
        self.assert_rejected("max_instances", max_instances=0)

    def test_negative_surge_is_rejected(self):
        self.assert_rejected("surge_instances", surge_instances=-1)

    def test_negative_reserve_is_rejected(self):
        self.assert_rejected("reserved_connections", reserved_connections=-5)

    def test_reserve_that_uses_the_whole_database_is_rejected(self):
        self.assert_rejected("must be below", reserved_connections=DB_MAX_CONNECTIONS)

    def test_tiny_desired_pool_is_rejected(self):
        self.assert_rejected("desired_pool_size", desired_pool_size=1)


if __name__ == "__main__":
    unittest.main()
