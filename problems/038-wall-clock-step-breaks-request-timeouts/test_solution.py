import unittest

from solution import (REQUEST_TIMEOUT_SECONDS, FakeHostClocks, TimeoutWatchdog,
                      naive_watchdog, safe_watchdog)

ONE_HOUR = 3600.0


def watchdog_with_stuck_request(factory):
    """A watchdog with one request started at t=0 on fresh host clocks."""
    clocks = FakeHostClocks()
    watchdog = factory(clocks)
    watchdog.start("stuck")
    return clocks, watchdog


class ReproduceProblem(unittest.TestCase):
    def test_wall_clock_step_back_hides_a_stuck_request(self):
        clocks, watchdog = watchdog_with_stuck_request(naive_watchdog)
        clocks.step_wall(-ONE_HOUR)
        clocks.advance(10 * 60)          # stuck for 10 minutes
        self.assertEqual(watchdog.timed_out(), [])
        self.assertLess(watchdog.elapsed("stuck"), 0)

    def test_wall_clock_step_forward_cancels_fresh_requests(self):
        clocks = FakeHostClocks()
        watchdog = naive_watchdog(clocks)
        for number in range(5):
            watchdog.start(f"req-{number}")
        clocks.advance(0.2)
        clocks.step_wall(ONE_HOUR)
        self.assertEqual(len(watchdog.timed_out()), 5)

    def test_wall_clock_reports_negative_latency(self):
        clocks = FakeHostClocks()
        watchdog = naive_watchdog(clocks)
        watchdog.start("fast")
        clocks.advance(0.05)
        clocks.step_wall(-2.0)
        self.assertAlmostEqual(watchdog.finish("fast"), -1.95)


class FixMonotonicDurations(unittest.TestCase):
    def test_stuck_request_times_out_despite_step_back(self):
        clocks, watchdog = watchdog_with_stuck_request(safe_watchdog)
        clocks.step_wall(-ONE_HOUR)
        clocks.advance(REQUEST_TIMEOUT_SECONDS)
        self.assertEqual(watchdog.timed_out(), ["stuck"])

    def test_fresh_requests_survive_step_forward(self):
        clocks = FakeHostClocks()
        watchdog = safe_watchdog(clocks)
        watchdog.start("req-1")
        clocks.advance(0.2)
        clocks.step_wall(ONE_HOUR)
        self.assertEqual(watchdog.timed_out(), [])

    def test_latency_is_real_elapsed_time(self):
        clocks = FakeHostClocks()
        watchdog = safe_watchdog(clocks)
        watchdog.start("fast")
        clocks.advance(0.05)
        clocks.step_wall(-2.0)
        self.assertAlmostEqual(watchdog.finish("fast"), 0.05)

    def test_request_just_under_timeout_is_kept(self):
        clocks, watchdog = watchdog_with_stuck_request(safe_watchdog)
        clocks.advance(REQUEST_TIMEOUT_SECONDS - 0.001)
        self.assertEqual(watchdog.timed_out(), [])
        clocks.advance(0.001)
        self.assertEqual(watchdog.timed_out(), ["stuck"])

    def test_finished_request_is_no_longer_tracked(self):
        clocks, watchdog = watchdog_with_stuck_request(safe_watchdog)
        watchdog.finish("stuck")
        clocks.advance(ONE_HOUR)
        self.assertEqual(watchdog.timed_out(), [])

    def test_non_positive_timeout_is_rejected(self):
        with self.assertRaises(ValueError):
            TimeoutWatchdog(FakeHostClocks().monotonic, timeout_seconds=0)


if __name__ == "__main__":
    unittest.main()
