import threading
import unittest

from solution import TokenBucketLimiter, handle_request


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


class TokenBucketTests(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.lim = TokenBucketLimiter(rate_per_s=10, capacity=20, clock=self.clock)

    def test_burst_up_to_capacity_then_blocked(self):
        allowed = [self.lim.allow("a").allowed for _ in range(25)]
        self.assertEqual(sum(allowed), 20)

    def test_refills_over_time(self):
        for _ in range(20):
            self.lim.allow("a")
        self.assertFalse(self.lim.allow("a").allowed)
        self.clock.t += 0.5                                   # 5 tokens back
        self.assertEqual(sum(self.lim.allow("a").allowed for _ in range(10)), 5)

    def test_retry_after_is_accurate(self):
        for _ in range(20):
            self.lim.allow("a")
        d = self.lim.allow("a")
        self.assertFalse(d.allowed)
        self.assertAlmostEqual(d.retry_after_s, 0.1, places=3)

    def test_clients_are_isolated(self):
        for _ in range(30):
            self.lim.allow("noisy")
        self.assertTrue(self.lim.allow("quiet").allowed)

    def test_sustained_rate_is_enforced(self):
        ok = 0
        for _ in range(1000):                                 # 10 s at 100 req/s offered
            self.clock.t += 0.01
            ok += self.lim.allow("a").allowed
        self.assertLessEqual(ok, 20 + 10 * 10 + 1)

    def test_http_429_with_retry_after(self):
        for _ in range(20):
            handle_request(self.lim, "k")
        status, headers = handle_request(self.lim, "k")
        self.assertEqual(status, 429)
        self.assertEqual(headers["Retry-After"], "1")

    def test_thread_safety_never_over_admits(self):
        lim = TokenBucketLimiter(rate_per_s=0.001, capacity=50)
        ok = []

        def worker():
            for _ in range(100):
                ok.append(lim.allow("x").allowed)

        ts = [threading.Thread(target=worker) for _ in range(8)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        self.assertEqual(sum(ok), 50)

    def test_lru_eviction_bounds_memory(self):
        lim = TokenBucketLimiter(10, 5, clock=self.clock, max_clients=3)
        for c in "abcd":
            lim.allow(c)
        self.assertEqual(len(lim._buckets), 3)


if __name__ == "__main__":
    unittest.main()
