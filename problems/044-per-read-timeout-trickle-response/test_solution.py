import unittest

from solution import (READ_TIMEOUT_SECONDS, TOTAL_DEADLINE_SECONDS, DeadlineExceeded,
                      FakeClock, FakeSlowSocket, download_with_deadline, naive_download,
                      trickle_plan)

TRICKLE_CHUNKS = 600
TRICKLE_GAP_SECONDS = 9.0  # just under the 10 s read timeout


def make_socket(plan: list[tuple[float, bytes]]) -> tuple[FakeSlowSocket, FakeClock]:
    clock = FakeClock()
    return FakeSlowSocket(clock, plan), clock


def make_trickling_socket() -> tuple[FakeSlowSocket, FakeClock]:
    return make_socket(trickle_plan(TRICKLE_CHUNKS, TRICKLE_GAP_SECONDS))


class ReproduceProblem(unittest.TestCase):
    def test_trickling_response_never_trips_the_read_timeout(self):
        sock, clock = make_trickling_socket()
        body = naive_download(sock)
        self.assertEqual(len(body), 60_000)
        self.assertEqual(clock.now, 5_400.0)  # 90 minutes for a "10 s timeout" call

    def test_read_timeout_only_fires_on_a_single_long_silence(self):
        sock, _ = make_socket([(READ_TIMEOUT_SECONDS + 1, b"late")])
        with self.assertRaises(TimeoutError):
            naive_download(sock)


class FixWithTotalDeadline(unittest.TestCase):
    def test_trickling_response_is_aborted_at_the_deadline(self):
        sock, clock = make_trickling_socket()
        with self.assertRaises(DeadlineExceeded):
            download_with_deadline(sock, clock)
        self.assertEqual(clock.now, TOTAL_DEADLINE_SECONDS)

    def test_last_read_waits_only_for_the_time_left(self):
        # Chunks at 9, 18, 27 s; the 4th read may wait 3 s, not 10 s.
        sock, clock = make_trickling_socket()
        with self.assertRaises(DeadlineExceeded):
            download_with_deadline(sock, clock)
        self.assertEqual(sock.reads, 4)
        self.assertLessEqual(clock.now, TOTAL_DEADLINE_SECONDS)

    def test_fast_response_is_returned_in_full(self):
        sock, clock = make_socket(trickle_plan(10, 0.2))
        self.assertEqual(len(download_with_deadline(sock, clock)), 1_000)

    def test_response_finishing_just_before_the_deadline_succeeds(self):
        sock, clock = make_socket(trickle_plan(3, 9.5))
        self.assertEqual(len(download_with_deadline(sock, clock)), 300)
        self.assertEqual(clock.now, 28.5)

    def test_long_silence_is_still_a_plain_read_timeout(self):
        sock, clock = make_socket([(READ_TIMEOUT_SECONDS + 1, b"late")])
        with self.assertRaises(TimeoutError) as caught:
            download_with_deadline(sock, clock)
        self.assertNotIsInstance(caught.exception, DeadlineExceeded)

    def test_deadline_error_says_how_much_arrived(self):
        sock, clock = make_trickling_socket()
        with self.assertRaisesRegex(DeadlineExceeded, "300 bytes"):
            download_with_deadline(sock, clock)

    def test_empty_response_returns_empty_body(self):
        sock, clock = make_socket([])
        self.assertEqual(download_with_deadline(sock, clock), b"")

    def test_non_positive_timeouts_are_rejected(self):
        sock, clock = make_trickling_socket()
        with self.assertRaises(ValueError):
            download_with_deadline(sock, clock, total_deadline=0)
        with self.assertRaises(ValueError):
            download_with_deadline(sock, clock, read_timeout=-1)


if __name__ == "__main__":
    unittest.main()
