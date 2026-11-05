import unittest

from solution import (DEADLINE_HEADER, Deadline, DeadlineExceeded, Dependency, FakeClock,
                      call_with_deadline, checkout_fixed_timeouts, checkout_with_deadline)

GATEWAY_TIMEOUT = 2.0


def deps(clock, inv, pri, pay):
    return Dependency(clock, inv), Dependency(clock, pri), Dependency(clock, pay)


class ReproduceRunawayWork(unittest.TestCase):
    def test_card_charged_after_gateway_gave_up(self):
        clock = FakeClock()
        inv, pri, pay = deps(clock, 1.5, 1.5, 0.4)
        result = checkout_fixed_timeouts(clock, inv, pri, pay)
        self.assertEqual(result, "charged")
        self.assertEqual(len(pay.calls), 1)
        # The charge happened at t=3.0s..3.4s; the customer saw a 504 at t=2.0s.
        self.assertAlmostEqual(clock.now(), 3.4)
        self.assertGreater(clock.now(), GATEWAY_TIMEOUT)

    def test_worst_case_is_three_times_the_gateway_budget(self):
        clock = FakeClock()
        inv, pri, pay = deps(clock, 1.9, 1.9, 1.9)
        checkout_fixed_timeouts(clock, inv, pri, pay)
        self.assertAlmostEqual(clock.now(), 5.7)


class SharedDeadline(unittest.TestCase):
    def test_payment_never_starts_when_budget_is_gone(self):
        clock = FakeClock()
        inv, pri, pay = deps(clock, 1.5, 1.5, 0.4)
        dl = Deadline.after(clock, GATEWAY_TIMEOUT)
        with self.assertRaises(TimeoutError):
            checkout_with_deadline(dl, inv, pri, pay)
        self.assertEqual(pri.calls, [0.5])     # pricing only got what was left
        self.assertEqual(pay.calls, [])        # card never charged
        self.assertAlmostEqual(clock.now(), GATEWAY_TIMEOUT)

    def test_payment_skipped_if_too_little_budget_remains(self):
        clock = FakeClock()
        inv, pri, pay = deps(clock, 0.9, 0.9, 0.1)
        dl = Deadline.after(clock, GATEWAY_TIMEOUT)
        with self.assertRaises(DeadlineExceeded):   # 0.2s left < 0.3s reserve
            checkout_with_deadline(dl, inv, pri, pay)
        self.assertEqual(pay.calls, [])

    def test_fast_path_unchanged(self):
        clock = FakeClock()
        inv, pri, pay = deps(clock, 0.1, 0.2, 0.3)
        dl = Deadline.after(clock, GATEWAY_TIMEOUT)
        self.assertEqual(checkout_with_deadline(dl, inv, pri, pay), "charged")
        self.assertEqual(inv.calls, [2.0])            # per-call cap still applies
        self.assertAlmostEqual(pay.calls[0], 1.7)

    def test_total_time_never_exceeds_budget(self):
        for lat in [(3, 3, 3), (1.99, 0.5, 0.5), (0.0, 1.95, 1.0), (0.5, 0.5, 0.99)]:
            clock = FakeClock()
            dl = Deadline.after(clock, GATEWAY_TIMEOUT)
            try:
                checkout_with_deadline(dl, *deps(clock, *lat))
            except (TimeoutError, DeadlineExceeded):
                pass
            self.assertLessEqual(clock.now(), GATEWAY_TIMEOUT + 1e-9, lat)

    def test_refuses_to_start_on_expired_deadline(self):
        clock = FakeClock()
        dl = Deadline.after(clock, 1.0)
        clock.advance(1.5)
        self.assertTrue(dl.expired())
        dep = Dependency(clock, 0.01)
        with self.assertRaises(DeadlineExceeded):
            call_with_deadline(dl, dep, per_call_cap=1.0)
        self.assertEqual(dep.calls, [])


class HeaderPropagation(unittest.TestCase):
    def test_round_trip_remaining_budget(self):
        clock = FakeClock(1000.0)
        dl = Deadline.after(clock, 2.0)
        clock.advance(0.75)
        headers = dl.to_header()
        self.assertEqual(headers, {DEADLINE_HEADER: "1250"})
        downstream_clock = FakeClock(5.0)          # different host, different clock
        child = Deadline.from_headers(downstream_clock, headers, default_s=10, max_s=30)
        self.assertAlmostEqual(child.remaining(), 1.25)

    def test_missing_or_malformed_header_uses_default(self):
        clock = FakeClock()
        self.assertEqual(Deadline.from_headers(clock, {}, 3.0, 30).remaining(), 3.0)
        bad = {DEADLINE_HEADER: "soon"}
        self.assertEqual(Deadline.from_headers(clock, bad, 3.0, 30).remaining(), 3.0)

    def test_huge_header_is_clamped_and_negative_is_expired(self):
        clock = FakeClock()
        big = Deadline.from_headers(clock, {DEADLINE_HEADER: "99999999"}, 3.0, 30)
        self.assertEqual(big.remaining(), 30)
        neg = Deadline.from_headers(clock, {DEADLINE_HEADER: "-5"}, 3.0, 30)
        self.assertTrue(neg.expired())

    def test_lowercased_header_from_http2_hop_keeps_the_budget(self):
        caller_clock = FakeClock()
        parent = Deadline.after(caller_clock, 2.0)
        caller_clock.advance(0.75)
        # HTTP/2 and many proxies deliver header names in lowercase.
        lowered = {name.lower(): value for name, value in parent.to_header().items()}
        child = Deadline.from_headers(FakeClock(), lowered, default_s=10, max_s=30)
        self.assertAlmostEqual(child.remaining(), 1.25)


if __name__ == "__main__":
    unittest.main()
