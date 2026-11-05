import threading
import unittest

from solution import ConnectionPool, FakeConnection, PoolTimeout, fixed_handler, leaky_handler


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def run(handler, pool, ids):
    ok = errors = timeouts = 0
    for i in ids:
        try:
            handler(pool, i)
            ok += 1
        except PoolTimeout:
            timeouts += 1
        except ValueError:
            errors += 1
    return ok, errors, timeouts


class ReproduceLeak(unittest.TestCase):
    def test_leaky_handler_exhausts_pool_after_max_size_errors(self):
        pool = ConnectionPool(max_size=5, acquire_timeout_s=0.02)
        # 5 bad requests leak all 5 connections ...
        self.assertEqual(run(leaky_handler, pool, [-1] * 5), (0, 5, 0))
        self.assertEqual(pool.in_use(), 5)
        # ... and every later *valid* request now fails.
        self.assertEqual(run(leaky_handler, pool, [1] * 10), (0, 0, 10))


class FixedHandler(unittest.TestCase):
    def test_errors_do_not_leak(self):
        pool = ConnectionPool(max_size=5, acquire_timeout_s=0.02)
        mix = [-1, 1] * 50
        self.assertEqual(run(fixed_handler, pool, mix), (50, 50, 0))
        self.assertEqual(pool.in_use(), 0)

    def test_failed_transaction_is_rolled_back_before_reuse(self):
        pool = ConnectionPool(max_size=1)
        with self.assertRaises(ValueError):
            fixed_handler(pool, -1)
        with pool.connection() as conn:
            self.assertEqual(conn.rolled_back, 1)
            self.assertTrue(conn.in_transaction)  # fresh transaction, not leftover

    def test_connection_with_failed_rollback_is_discarded(self):
        pool = ConnectionPool(max_size=1)

        def broken_rollback():
            raise ConnectionError("socket closed")

        with self.assertRaises(RuntimeError):
            with pool.connection() as conn:
                first = conn
                conn.rollback = broken_rollback
                raise RuntimeError("query failed")
        self.assertTrue(first.closed)
        self.assertEqual(pool.stats["discarded"], 1)
        with pool.connection() as conn:   # slot was freed, new connection made
            self.assertIsNot(conn, first)

    def test_never_exceeds_max_size_under_concurrency(self):
        pool = ConnectionPool(max_size=4, acquire_timeout_s=2)
        peak = [0]
        lock = threading.Lock()
        barrier = threading.Barrier(16)

        def worker():
            barrier.wait()
            for _ in range(50):
                with pool.connection():
                    with lock:
                        peak[0] = max(peak[0], pool.in_use())

        threads = [threading.Thread(target=worker) for _ in range(16)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertLessEqual(peak[0], 4)
        self.assertEqual(pool.in_use(), 0)
        self.assertEqual(pool.stats["acquired"], 16 * 50)

    def test_waiter_is_woken_when_connection_returns(self):
        pool = ConnectionPool(max_size=1, acquire_timeout_s=2)
        conn = pool.acquire()
        got = []
        t = threading.Thread(target=lambda: got.append(pool.acquire()))
        t.start()
        threading.Timer(0.05, pool.release, args=(conn,)).start()
        t.join(3)
        self.assertEqual(got, [conn])


class TimeoutsAndLeakDetection(unittest.TestCase):
    def test_acquire_times_out_instead_of_hanging(self):
        pool = ConnectionPool(max_size=1, acquire_timeout_s=0.05)
        pool.acquire()
        with self.assertRaises(PoolTimeout):
            pool.acquire()
        self.assertEqual(pool.stats["timeouts"], 1)

    def test_leak_report_points_at_checkout_site(self):
        clock = FakeClock()
        pool = ConnectionPool(max_size=3, clock=clock)
        with self.assertRaises(ValueError):
            leaky_handler(pool, -1)
        clock.t = 31
        with pool.connection():
            report = pool.leak_report(older_than_s=30)
        self.assertEqual(len(report), 1)
        _, held, stack = report[0]
        self.assertEqual(held, 31)
        self.assertIn("leaky_handler", stack)

    def test_double_release_is_rejected(self):
        pool = ConnectionPool(max_size=1)
        conn = pool.acquire()
        pool.release(conn)
        with self.assertRaises(ValueError):
            pool.release(conn)

    def test_invalid_size(self):
        with self.assertRaises(ValueError):
            ConnectionPool(max_size=0)


class FlakyFactory:
    """Connection factory that fails while ``down`` is True (a DB restart)."""

    def __init__(self) -> None:
        self.down = True

    def __call__(self) -> FakeConnection:
        if self.down:
            raise ConnectionError("database is restarting")
        return FakeConnection()


class FailedConnectsDoNotLeakSlots(unittest.TestCase):
    def test_pool_recovers_after_max_size_failed_connects(self):
        factory = FlakyFactory()
        pool = ConnectionPool(factory=factory, max_size=2, acquire_timeout_s=0.05)
        for _ in range(2):
            with self.assertRaises(ConnectionError):
                pool.acquire()
        factory.down = False
        conn = pool.acquire()
        self.assertFalse(conn.closed)
        self.assertEqual(pool.in_use(), 1)

    def test_pool_can_still_fill_to_max_size_after_failures(self):
        factory = FlakyFactory()
        pool = ConnectionPool(factory=factory, max_size=2, acquire_timeout_s=0.05)
        with self.assertRaises(ConnectionError):
            pool.acquire()
        factory.down = False
        pool.acquire()
        pool.acquire()
        with self.assertRaises(PoolTimeout):
            pool.acquire()


if __name__ == "__main__":
    unittest.main()
