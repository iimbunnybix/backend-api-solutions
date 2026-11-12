import unittest

from solution import (INT32_MAX, DailyWorkload, HeadroomMonitor, IdAllocator, IdSpaceExhausted,
                      OrdersTable, migration_problems, naive_should_alert, next_power_of_two,
                      simulate)

# Same rows per day as the default workload, but nothing burned.
NO_WASTE_WORKLOAD = DailyWorkload(committed_orders=6_000_000, rolled_back_orders=0, bulk_batches=0)


def monitor_with_daily_rate(ids_per_day: int, days: int, start_id: int = 0) -> HeadroomMonitor:
    """A monitor fed `days` readings of a counter growing at a constant rate."""
    monitor = HeadroomMonitor()
    for day in range(days):
        monitor.record(day, start_id + day * ids_per_day)
    return monitor


class ReproduceProblem(unittest.TestCase):
    def test_insert_past_the_int32_limit_fails(self):
        allocator = IdAllocator(next_id=INT32_MAX)
        allocator.allocate(1)
        with self.assertRaises(IdSpaceExhausted):
            allocator.allocate(1)

    def test_rolled_back_insert_burns_ids_without_adding_rows(self):
        table = OrdersTable(IdAllocator(next_id=1), row_count=0)
        table.insert_orders(10, committed=False)
        self.assertEqual((table.row_count, table.allocator.last_allocated), (0, 10))

    def test_bulk_insert_reserves_more_ids_than_rows(self):
        table = OrdersTable(IdAllocator(next_id=1), row_count=0)
        table.bulk_insert(10_000)
        self.assertEqual((table.row_count, table.allocator.last_allocated), (10_000, 16_384))

    def test_row_count_monitor_never_alerts_before_inserts_fail(self):
        result = simulate()
        self.assertIsNone(result.naive_alert_day)
        self.assertAlmostEqual(result.row_fraction_at_exhaustion, 0.709, places=3)

    def test_burned_ids_bring_exhaustion_forward(self):
        self.assertEqual(simulate().exhaustion_day, 186)
        self.assertEqual(simulate(workload=NO_WASTE_WORKLOAD).exhaustion_day, 257)


class FixHeadroomMonitor(unittest.TestCase):
    def test_alert_fires_with_the_full_lead_time_before_exhaustion(self):
        result = simulate()
        self.assertEqual(result.headroom_alert_day, 97)
        self.assertEqual(result.exhaustion_day - result.headroom_alert_day, 89)

    def test_forecast_is_remaining_ids_divided_by_rate(self):
        monitor = monitor_with_daily_rate(ids_per_day=1_000, days=8, start_id=INT32_MAX - 107_000)
        self.assertEqual(monitor.days_until_exhaustion(), 100.0)

    def test_no_alert_while_far_from_the_limit(self):
        monitor = monitor_with_daily_rate(ids_per_day=1_000, days=8)
        self.assertFalse(monitor.should_alert())

    def test_forecast_follows_a_recent_jump_in_traffic(self):
        monitor = monitor_with_daily_rate(ids_per_day=1_000, days=30)
        last_day, last_id = monitor.readings[-1]
        for extra_day in range(1, 8):
            monitor.record(last_day + extra_day, last_id + extra_day * 10_000)
        # Only the last 7 days count, so the rate is the new 10,000/day.
        self.assertEqual(monitor.ids_per_day(), 10_000)

    def test_idle_counter_has_no_forecast(self):
        monitor = monitor_with_daily_rate(ids_per_day=0, days=8, start_id=5)
        self.assertIsNone(monitor.days_until_exhaustion())
        self.assertFalse(monitor.should_alert())

    def test_single_reading_has_no_forecast(self):
        monitor = monitor_with_daily_rate(ids_per_day=1_000, days=1)
        self.assertIsNone(monitor.days_until_exhaustion())

    def test_exhausted_counter_reports_zero_days(self):
        monitor = monitor_with_daily_rate(ids_per_day=0, days=2, start_id=INT32_MAX)
        self.assertEqual(monitor.days_until_exhaustion(), 0.0)
        self.assertTrue(monitor.should_alert())


class FixMigrationCheck(unittest.TestCase):
    def test_int_primary_key_and_foreign_key_are_both_flagged(self):
        columns = {"orders.id": "INT", "order_items.order_id": "int", "payments.order_id": "bigint"}
        problems = migration_problems(columns, "orders.id", ["order_items.order_id", "payments.order_id"])
        self.assertEqual(problems, ["orders.id is int; migrate it to bigint",
                                    "order_items.order_id is int; migrate it to bigint"])

    def test_fully_migrated_schema_has_no_problems(self):
        columns = {"orders.id": "BIGINT", "order_items.order_id": "int8"}
        self.assertEqual(migration_problems(columns, "orders.id", ["order_items.order_id"]), [])

    def test_bigint_allocator_survives_where_int_failed(self):
        allocator = IdAllocator(next_id=INT32_MAX, max_id=2**63 - 1)
        allocator.allocate(next_power_of_two(10_000))
        self.assertGreater(allocator.last_allocated, INT32_MAX)


if __name__ == "__main__":
    unittest.main()
