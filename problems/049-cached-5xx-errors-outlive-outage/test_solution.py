import unittest

from solution import (ERROR_MAX_AGE_CAP_SECONDS, HTTP_OK, HTTP_SERVICE_UNAVAILABLE,
                      FakeClock, FixedProxyCache, FlakyOrigin, NaiveProxyCache, Response,
                      fixed_cache_headers, naive_cache_headers, parse_cache_control,
                      simulate_outage, storable_entry)

OUTAGE_START = 100
OUTAGE_END = 130
PATH = "/products/1"
# simulate_outage defaults: outage from second 600 for 30 seconds.
SIMULATED_RECOVERY_SECOND = 630


def proxy_in_front_of_flaky_origin(proxy_class: type, middleware, outage_start: int = OUTAGE_START,
                                   outage_end: int = OUTAGE_END) -> tuple[FakeClock, FlakyOrigin, object]:
    """A proxy whose origin is down from outage_start until outage_end."""
    clock = FakeClock()
    origin = FlakyOrigin(clock, outage_start, outage_end)

    def fetch(path: str) -> Response:
        return middleware(origin.handle(path))

    return clock, origin, proxy_class(clock, fetch)


def status_at(clock: FakeClock, proxy, second: int) -> int:
    """Move the clock and return the status a client gets."""
    clock.now = second
    return proxy.get(PATH).status


class ReproduceProblem(unittest.TestCase):
    def test_global_middleware_marks_a_503_cacheable(self):
        response = naive_cache_headers(Response(HTTP_SERVICE_UNAVAILABLE, "down"))
        self.assertIn("max-age=300", response.headers["Cache-Control"])

    def test_error_cached_during_outage_is_served_long_after_recovery(self):
        clock, _, proxy = proxy_in_front_of_flaky_origin(NaiveProxyCache, naive_cache_headers)
        status_at(clock, proxy, OUTAGE_START + 1)
        self.assertEqual(status_at(clock, proxy, OUTAGE_END + 250), HTTP_SERVICE_UNAVAILABLE)

    def test_thirty_second_blip_breaks_thirty_urls_for_almost_five_minutes(self):
        report = simulate_outage(naive_cache_headers, NaiveProxyCache)
        self.assertEqual(report.urls_broken_after_recovery, 30)
        self.assertEqual(report.errors_after_recovery, 8535)
        self.assertEqual(report.last_error_second, 928)


class FixOriginHeaders(unittest.TestCase):
    def test_server_error_is_marked_no_store(self):
        response = fixed_cache_headers(Response(HTTP_SERVICE_UNAVAILABLE, "down"))
        self.assertEqual(response.headers["Cache-Control"], "no-store")

    def test_success_is_cacheable_with_stale_if_error(self):
        directives = parse_cache_control(fixed_cache_headers(Response(HTTP_OK, "ok")).headers["Cache-Control"])
        self.assertEqual(directives["max-age"], 300)
        self.assertEqual(directives["stale-if-error"], 600)

    def test_even_the_naive_proxy_recovers_with_the_origin(self):
        report = simulate_outage(fixed_cache_headers, NaiveProxyCache)
        self.assertEqual(report.errors_after_recovery, 0)
        # The last error is served in the final second of the outage, never after.
        self.assertEqual(report.last_error_second, 629)


class FixProxyCache(unittest.TestCase):
    def test_no_store_response_is_never_stored(self):
        response = Response(HTTP_SERVICE_UNAVAILABLE, "down", {"Cache-Control": "no-store, max-age=300"})
        self.assertIsNone(storable_entry(response, now=0))

    def test_error_ttl_is_capped_for_a_misconfigured_origin(self):
        entry = storable_entry(naive_cache_headers(Response(HTTP_SERVICE_UNAVAILABLE, "down")), now=0)
        self.assertEqual(entry.max_age, ERROR_MAX_AGE_CAP_SECONDS)

    def test_error_never_gets_stale_if_error(self):
        headers = {"Cache-Control": "max-age=60, stale-if-error=600"}
        entry = storable_entry(Response(HTTP_SERVICE_UNAVAILABLE, "down", headers), now=0)
        self.assertEqual(entry.stale_if_error, 0)

    def test_misconfigured_origin_errors_stop_within_the_cap(self):
        report = simulate_outage(naive_cache_headers, FixedProxyCache)
        self.assertLess(report.last_error_second, SIMULATED_RECOVERY_SECOND + ERROR_MAX_AGE_CAP_SECONDS)

    def test_stale_good_copy_is_served_while_origin_is_down(self):
        clock, _, proxy = proxy_in_front_of_flaky_origin(FixedProxyCache, fixed_cache_headers)
        status_at(clock, proxy, 0)
        clock.now = OUTAGE_START + 1
        self.assertEqual(proxy.get(PATH).body, f"page {PATH}")

    def test_stale_copy_is_not_served_past_the_stale_if_error_window(self):
        # A long outage: from second 800 until well after the stale window ends.
        clock, _, proxy = proxy_in_front_of_flaky_origin(FixedProxyCache, fixed_cache_headers, 800, 2000)
        status_at(clock, proxy, 0)
        # Stored at 0 with max-age 300 + stale-if-error 600: still usable at 899, not at 900.
        self.assertEqual(status_at(clock, proxy, 899), HTTP_OK)
        self.assertEqual(status_at(clock, proxy, 900), HTTP_SERVICE_UNAVAILABLE)

    def test_fresh_copy_is_fetched_once_origin_recovers(self):
        clock, origin, proxy = proxy_in_front_of_flaky_origin(FixedProxyCache, fixed_cache_headers)
        status_at(clock, proxy, OUTAGE_START + 1)
        calls_before = origin.calls
        self.assertEqual(status_at(clock, proxy, OUTAGE_END), HTTP_OK)
        self.assertEqual(origin.calls, calls_before + 1)

    def test_outage_is_invisible_to_clients_with_both_fixes(self):
        report = simulate_outage(fixed_cache_headers, FixedProxyCache)
        self.assertEqual(report.errors_during_outage + report.errors_after_recovery, 0)


if __name__ == "__main__":
    unittest.main()
