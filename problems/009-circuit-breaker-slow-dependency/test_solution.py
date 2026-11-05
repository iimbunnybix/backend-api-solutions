import unittest

from solution import (CircuitBreaker, CircuitOpenError, FakeClock, FlakyDependency, State,
                      worker_capacity_rps)

OWN_WORK = 0.02          # seconds the page handler spends on its own work
ARRIVAL_GAP = 0.005      # 200 page requests per second
FALLBACK = lambda: []    # noqa: E731  render the page without recommendations


def serve(n, clock, recs, breaker=None):
    """Serve n product pages; return total worker-seconds spent waiting on recs."""
    waited = 0.0
    for _ in range(n):
        clock.advance(ARRIVAL_GAP)
        start = clock.now()
        if breaker is None:
            try:
                recs()
            except TimeoutError:
                pass
        else:
            breaker.call(recs, fallback=FALLBACK)
        waited += clock.now() - start
    return waited


def make(clock, **kw):
    params = dict(window_size=20, min_calls=10, failure_rate_threshold=0.5,
                  slow_call_seconds=1.0, open_seconds=30.0, half_open_max_calls=3,
                  clock=clock.now)
    params.update(kw)
    return CircuitBreaker(**params)


class ReproduceThreadExhaustion(unittest.TestCase):
    def test_every_request_waits_the_full_timeout(self):
        clock = FakeClock()
        recs = FlakyDependency(clock)
        recs.down = True
        waited = serve(1000, clock, recs)
        self.assertEqual(recs.calls, 1000)
        self.assertAlmostEqual(waited, 3000.0)

    def test_pool_capacity_collapses(self):
        healthy = worker_capacity_rps(40, 0.05 + OWN_WORK)
        outage = worker_capacity_rps(40, 3.0 + OWN_WORK)
        self.assertAlmostEqual(healthy, 571.4, places=1)
        self.assertAlmostEqual(outage, 13.2, places=1)
        self.assertLess(outage, 200)        # 200 req/s arriving -> queue grows without bound


class BreakerFailsFast(unittest.TestCase):
    def test_trips_after_min_calls_and_stops_calling(self):
        clock = FakeClock()
        recs = FlakyDependency(clock)
        recs.down = True
        cb = make(clock)
        waited = serve(1000, clock, recs, cb)
        self.assertEqual(recs.calls, 10)            # only the calls that tripped it
        self.assertAlmostEqual(waited, 30.0)        # vs 3000 s without the breaker
        self.assertEqual(cb.short_circuited, 990)
        self.assertEqual(cb.state, State.OPEN)

    def test_slow_successes_count_as_failures(self):
        clock = FakeClock()
        recs = FlakyDependency(clock, latency=2.5)   # answers, but too slowly
        cb = make(clock)
        serve(10, clock, recs, cb)
        self.assertEqual(cb.state, State.OPEN)

    def test_does_not_trip_below_min_calls(self):
        clock = FakeClock()
        recs = FlakyDependency(clock)
        recs.down = True
        cb = make(clock)
        serve(9, clock, recs, cb)
        self.assertEqual(cb.state, State.CLOSED)

    def test_occasional_errors_do_not_trip(self):
        clock = FakeClock()
        recs = FlakyDependency(clock)
        cb = make(clock)
        for i in range(200):
            recs.down = (i % 5 == 0)                 # 20% failures < 50% threshold
            cb.call(recs, fallback=FALLBACK)
        self.assertEqual(cb.state, State.CLOSED)

    def test_raises_without_fallback(self):
        clock = FakeClock()
        recs = FlakyDependency(clock)
        recs.down = True
        cb = make(clock)
        for _ in range(10):
            with self.assertRaises(TimeoutError):
                cb.call(recs)
        with self.assertRaises(CircuitOpenError):
            cb.call(recs)


class Recovery(unittest.TestCase):
    def _open(self):
        clock = FakeClock()
        recs = FlakyDependency(clock)
        recs.down = True
        cb = make(clock)
        serve(10, clock, recs, cb)
        self.assertEqual(cb.state, State.OPEN)
        return clock, recs, cb

    def test_half_open_then_close_after_successful_probes(self):
        clock, recs, cb = self._open()
        recs.down = False
        clock.advance(30.0)
        self.assertEqual(cb.state, State.HALF_OPEN)
        for _ in range(3):
            self.assertEqual(cb.call(recs, fallback=FALLBACK), ["sku-1", "sku-2", "sku-3"])
        self.assertEqual(cb.state, State.CLOSED)

    def test_failed_probe_reopens(self):
        clock, recs, cb = self._open()
        clock.advance(30.0)
        calls_before = recs.calls
        cb.call(recs, fallback=FALLBACK)          # still down -> probe fails
        self.assertEqual(cb.state, State.OPEN)
        cb.call(recs, fallback=FALLBACK)          # short-circuited again
        self.assertEqual(recs.calls, calls_before + 1)

    def test_probe_concurrency_is_limited(self):
        clock, recs, cb = self._open()
        clock.advance(30.0)
        for _ in range(3):
            cb._acquire()                          # three probes in flight
        with self.assertRaises(CircuitOpenError):
            cb._acquire()

    def test_probes_interrupted_by_base_exception_release_their_slots(self):
        clock, recs, cb = self._open()
        clock.advance(30.0)

        class Cancelled(BaseException):
            """Stands in for asyncio.CancelledError / gevent.Timeout."""

        def cancelled_call():
            raise Cancelled()

        for _ in range(3):
            with self.assertRaises(Cancelled):
                cb.call(cancelled_call, fallback=FALLBACK)
        recs.down = False
        calls_before = recs.calls
        for _ in range(3):
            self.assertEqual(cb.call(recs, fallback=FALLBACK), ["sku-1", "sku-2", "sku-3"])
        self.assertEqual(recs.calls, calls_before + 3)
        self.assertEqual(cb.state, State.CLOSED)

    def test_invalid_config(self):
        with self.assertRaises(ValueError):
            CircuitBreaker(failure_rate_threshold=0)
        with self.assertRaises(ValueError):
            CircuitBreaker(window_size=5, min_calls=10)


if __name__ == "__main__":
    unittest.main()
