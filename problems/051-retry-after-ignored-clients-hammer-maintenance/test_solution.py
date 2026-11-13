import unittest
from email.utils import format_datetime
from datetime import datetime, timezone

from solution import (
    MAX_ATTEMPTS,
    MAX_RETRY_AFTER_SECONDS,
    Response,
    naive_get,
    parse_retry_after,
    polite_get,
)

START_EPOCH = 1_800_000_000.0
MAINTENANCE_SECONDS = 120


class FakeClock:
    """Clock that only moves when the code under test sleeps."""

    def __init__(self) -> None:
        self.current = START_EPOCH
        self.sleeps: list[float] = []

    def now(self) -> float:
        return self.current

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.current += seconds


class MaintenanceServer:
    """Returns 503 + Retry-After until maintenance ends, then 200."""

    def __init__(self, clock: FakeClock, retry_after: str | None = str(MAINTENANCE_SECONDS)) -> None:
        self.clock = clock
        self.retry_after = retry_after
        self.ends_at = START_EPOCH + MAINTENANCE_SECONDS
        self.requests = 0

    def send(self, path: str) -> Response:
        self.requests += 1
        if self.clock.now() >= self.ends_at:
            return Response(200, body="stock: 7")
        headers = {} if self.retry_after is None else {"Retry-After": self.retry_after}
        return Response(503, headers=headers)


class ReproduceProblem(unittest.TestCase):
    def test_naive_client_gives_up_while_maintenance_is_still_running(self):
        clock = FakeClock()
        server = MaintenanceServer(clock)
        response = naive_get(server, clock, "/stock/42")
        self.assertEqual(response.status, 503)
        self.assertEqual(server.requests, MAX_ATTEMPTS)
        self.assertLess(clock.now() - START_EPOCH, 2.0)


class FixHonourRetryAfter(unittest.TestCase):
    def test_client_waits_as_told_and_succeeds_after_maintenance(self):
        clock = FakeClock()
        server = MaintenanceServer(clock)
        response = polite_get(server, clock, "/stock/42", deadline_seconds=300)
        self.assertEqual(response.status, 200)
        self.assertEqual(server.requests, 2)
        self.assertEqual(clock.sleeps, [120.0])

    def test_http_date_form_is_understood(self):
        clock = FakeClock()
        retry_at = datetime.fromtimestamp(START_EPOCH + MAINTENANCE_SECONDS, tz=timezone.utc)
        server = MaintenanceServer(clock, retry_after=format_datetime(retry_at, usegmt=True))
        response = polite_get(server, clock, "/stock/42", deadline_seconds=300)
        self.assertEqual(response.status, 200)
        self.assertEqual(server.requests, 2)

    def test_client_fails_fast_when_wait_exceeds_deadline(self):
        clock = FakeClock()
        server = MaintenanceServer(clock)
        response = polite_get(server, clock, "/stock/42", deadline_seconds=10)
        self.assertEqual(response.status, 503)
        self.assertEqual(server.requests, 1)
        self.assertEqual(clock.sleeps, [])

    def test_missing_header_falls_back_to_exponential_backoff(self):
        clock = FakeClock()
        server = MaintenanceServer(clock, retry_after=None)
        polite_get(server, clock, "/stock/42", deadline_seconds=300)
        self.assertEqual(clock.sleeps, [0.1, 0.2, 0.4, 0.8])


class FixParseRetryAfter(unittest.TestCase):
    def test_seconds_form(self):
        self.assertEqual(parse_retry_after("30", START_EPOCH), 30.0)

    def test_date_in_the_past_means_retry_now(self):
        past = datetime.fromtimestamp(START_EPOCH - 60, tz=timezone.utc)
        self.assertEqual(parse_retry_after(format_datetime(past, usegmt=True), START_EPOCH), 0.0)

    def test_huge_value_is_capped(self):
        self.assertEqual(parse_retry_after("86400", START_EPOCH), MAX_RETRY_AFTER_SECONDS)

    def test_garbage_and_negative_values_are_ignored(self):
        self.assertIsNone(parse_retry_after("soon", START_EPOCH))
        self.assertIsNone(parse_retry_after("-5", START_EPOCH))
        self.assertIsNone(parse_retry_after(None, START_EPOCH))


if __name__ == "__main__":
    unittest.main()
