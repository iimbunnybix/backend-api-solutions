import unittest
from datetime import date

from solution import (
    add_months_clamped,
    days_lost_in_first_year,
    naive_schedule,
    next_renewal_after,
    renewal_date,
    schedule,
)

JAN_31_SIGNUP = date(2025, 1, 31)


class ReproduceProblem(unittest.TestCase):
    def test_january_31_customer_is_stuck_on_the_28th_after_february(self):
        renewals = naive_schedule(JAN_31_SIGNUP, 4)
        self.assertEqual([renewal.day for renewal in renewals], [28, 28, 28, 28])

    def test_naive_schedule_loses_29_days_in_the_first_year(self):
        self.assertEqual(days_lost_in_first_year(JAN_31_SIGNUP), 29)

    def test_a_year_later_naive_renewal_is_three_days_early(self):
        self.assertEqual(naive_schedule(JAN_31_SIGNUP, 12)[-1], date(2026, 1, 28))


class FixAnchorsToSignupDay(unittest.TestCase):
    def test_renewal_returns_to_the_31st_after_february(self):
        self.assertEqual(schedule(JAN_31_SIGNUP, 4), [
            date(2025, 2, 28), date(2025, 3, 31), date(2025, 4, 30), date(2025, 5, 31),
        ])

    def test_thirty_day_months_clamp_to_the_30th(self):
        self.assertEqual(renewal_date(JAN_31_SIGNUP, 3), date(2025, 4, 30))

    def test_twelfth_renewal_lands_on_the_anniversary(self):
        self.assertEqual(renewal_date(JAN_31_SIGNUP, 12), date(2026, 1, 31))

    def test_leap_year_february_gets_the_29th(self):
        self.assertEqual(renewal_date(date(2024, 1, 31), 1), date(2024, 2, 29))

    def test_leap_day_signup_renews_on_the_28th_in_a_common_year(self):
        self.assertEqual(renewal_date(date(2024, 2, 29), 12), date(2025, 2, 28))

    def test_mid_month_signup_is_unchanged_by_the_fix(self):
        signup = date(2025, 1, 15)
        self.assertEqual(schedule(signup, 12), naive_schedule(signup, 12))
        self.assertEqual(days_lost_in_first_year(signup), 0)

    def test_december_signup_rolls_into_the_next_year(self):
        self.assertEqual(add_months_clamped(date(2025, 12, 31), 2), date(2026, 2, 28))

    def test_cycle_number_must_be_positive(self):
        with self.assertRaises(ValueError):
            renewal_date(JAN_31_SIGNUP, 0)


class FixNextRenewalAfter(unittest.TestCase):
    def test_on_signup_day_next_renewal_is_the_first_cycle(self):
        self.assertEqual(next_renewal_after(JAN_31_SIGNUP, JAN_31_SIGNUP), date(2025, 2, 28))

    def test_on_a_renewal_day_next_renewal_is_the_following_one(self):
        self.assertEqual(next_renewal_after(JAN_31_SIGNUP, date(2025, 2, 28)), date(2025, 3, 31))

    def test_early_in_a_month_next_renewal_is_later_that_month(self):
        self.assertEqual(next_renewal_after(JAN_31_SIGNUP, date(2025, 3, 5)), date(2025, 3, 31))

    def test_matches_the_anchored_schedule_for_every_day_of_two_years(self):
        renewals = schedule(JAN_31_SIGNUP, 30)
        today = JAN_31_SIGNUP
        while today < date(2027, 1, 31):
            expected = next(renewal for renewal in renewals if renewal > today)
            self.assertEqual(next_renewal_after(JAN_31_SIGNUP, today), expected)
            today = date.fromordinal(today.toordinal() + 1)


if __name__ == "__main__":
    unittest.main()
