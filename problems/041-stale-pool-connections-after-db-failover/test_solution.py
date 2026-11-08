import unittest
from functools import partial
from typing import Callable

from solution import (IDLE_PING_THRESHOLD_SECONDS, MAX_LIFETIME_SECONDS, ConnectionBroken,
                      FakeCluster, NaivePool, QueryError, ReadOnlyServer, ValidatingPool,
                      naive_run_query, run_query)

POOL_SIZE = 10
REQUESTS_AFTER_RESTART = 50
WRITE_SQL = "INSERT INTO orders VALUES (1)"


class FakeClock:
    """Manually advanced clock in seconds."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def warm_naive_pool(cluster: FakeCluster) -> NaivePool:
    """A naive pool holding POOL_SIZE idle connections."""
    pool = NaivePool(cluster)
    for _ in range(POOL_SIZE):
        pool.release(cluster.connect())
    return pool


def warm_validating_pool(cluster: FakeCluster, clock: FakeClock) -> ValidatingPool:
    """A validating pool holding POOL_SIZE idle connections."""
    pool = ValidatingPool(cluster, clock)
    opened = [pool.acquire() for _ in range(POOL_SIZE)]
    for conn in opened:
        pool.release(conn)
    return pool


def count_failures(query: Callable[[str], str], sql: str, attempts: int) -> int:
    """How many of ``attempts`` calls to ``query(sql)`` raised a DB error."""
    failures = 0
    for _ in range(attempts):
        try:
            query(sql)
        except (ConnectionBroken, ReadOnlyServer):
            failures += 1
    return failures


class ReproduceProblem(unittest.TestCase):
    def test_every_request_fails_after_a_database_restart(self):
        cluster = FakeCluster()
        pool = warm_naive_pool(cluster)
        cluster.primary.restart()
        failures = count_failures(partial(naive_run_query, pool),
                                  "SELECT * FROM orders", REQUESTS_AFTER_RESTART)
        # Dead connections go back into the pool, so it never recovers.
        self.assertEqual(failures, REQUESTS_AFTER_RESTART)

    def test_writes_keep_hitting_the_old_primary_after_failover(self):
        cluster = FakeCluster()
        pool = warm_naive_pool(cluster)
        cluster.fail_over()
        failures = count_failures(partial(naive_run_query, pool),
                                  WRITE_SQL, REQUESTS_AFTER_RESTART)
        self.assertEqual(failures, REQUESTS_AFTER_RESTART)


class FixValidateOnCheckout(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = FakeClock()
        self.cluster = FakeCluster()
        self.pool = warm_validating_pool(self.cluster, self.clock)

    def query(self, sql: str) -> str:
        return run_query(self.pool, sql)

    def test_idle_connections_are_pinged_and_replaced_after_restart(self):
        self.clock.advance(IDLE_PING_THRESHOLD_SECONDS + 1)
        self.cluster.primary.restart()
        failures = count_failures(self.query, "SELECT 1", REQUESTS_AFTER_RESTART)
        self.assertEqual(failures, 0)

    def test_recently_used_connection_is_not_pinged(self):
        self.clock.advance(IDLE_PING_THRESHOLD_SECONDS - 1)
        self.query("SELECT * FROM orders")
        self.assertEqual(self.pool.pings_sent, 0)

    def test_restart_within_threshold_costs_at_most_one_failed_request(self):
        # Not idle long enough to ping, so the first query finds out the hard
        # way; that one disconnect then invalidates every older connection.
        self.cluster.primary.restart()
        failures = count_failures(self.query, "SELECT 1", REQUESTS_AFTER_RESTART)
        self.assertEqual(failures, 1)
        self.assertEqual(self.pool.discarded, POOL_SIZE)
        self.assertEqual(self.cluster.connections_opened, POOL_SIZE + 1)

    def test_broken_connection_is_closed_not_returned(self):
        self.cluster.primary.restart()
        conn = self.pool.idle[-1]
        with self.assertRaises(ConnectionBroken):
            self.query("SELECT 1")
        self.assertTrue(conn.closed)
        self.assertNotIn(conn, self.pool.idle)

    def test_ordinary_query_error_returns_connection_to_pool(self):
        conn = self.pool.idle[-1]
        with self.assertRaises(QueryError):
            self.query("SELEKT 1")
        self.assertIn(conn, self.pool.idle)
        self.assertEqual(self.pool.discarded, 0)


class FixMaxLifetime(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = FakeClock()
        self.cluster = FakeCluster()
        self.pool = warm_validating_pool(self.cluster, self.clock)

    def test_connection_older_than_max_lifetime_is_replaced_on_checkout(self):
        self.clock.advance(MAX_LIFETIME_SECONDS)
        opened_before = self.cluster.connections_opened
        conn = self.pool.acquire()
        self.assertEqual(conn.created_at, self.clock.now)
        self.assertEqual(self.cluster.connections_opened, opened_before + 1)
        self.assertEqual(self.pool.discarded, POOL_SIZE)

    def test_connection_expiring_while_checked_out_is_closed_on_release(self):
        conn = self.pool.acquire()
        self.clock.advance(MAX_LIFETIME_SECONDS)
        self.pool.release(conn)
        self.assertTrue(conn.closed)

    def test_pool_moves_to_new_primary_after_failover(self):
        # The old primary still answers SELECT 1, so a ping alone cannot
        # tell; the lifetime limit bounds how long writes can go there.
        self.cluster.fail_over()
        self.clock.advance(MAX_LIFETIME_SECONDS)
        result = run_query(self.pool, WRITE_SQL)
        self.assertEqual(result, f"ok from {self.cluster.primary.name}")

    def test_read_only_error_after_failover_reconnects_all_connections(self):
        self.cluster.fail_over()
        failures = count_failures(partial(run_query, self.pool),
                                  WRITE_SQL, REQUESTS_AFTER_RESTART)
        self.assertEqual(failures, 1)


if __name__ == "__main__":
    unittest.main()
