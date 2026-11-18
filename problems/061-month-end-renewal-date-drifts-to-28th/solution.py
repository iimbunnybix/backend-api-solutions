"""Monthly renewal dates that stay on the customer's billing day.

The subscription service computed each renewal by adding one month to the
PREVIOUS renewal date and clamping to the end of the month. A customer who
signed up on 31 January renewed on 28 February, and from then on on the 28th
of every month, because the clamped date became the new starting point. The
fix keeps the original "anchor" day and computes every renewal from the
signup date, clamping only for the month being computed.
"""
from __future__ import annotations

import calendar
from datetime import date

MONTHS_PER_YEAR = 12


def days_in_month(year: int, month: int) -> int:
    """Number of days in the given month (handles leap years)."""
    return calendar.monthrange(year, month)[1]


def add_months_clamped(start: date, months: int) -> date:
    """Move ``start`` forward by whole months, clamping the day to month end.

    31 Jan + 1 month has no 31 Feb, so the result is the last day of February.
    """
    # Work in a zero-based month count so the year carries correctly.
    month_index = start.year * MONTHS_PER_YEAR + (start.month - 1) + months
    year = month_index // MONTHS_PER_YEAR
    month = month_index % MONTHS_PER_YEAR + 1
    day = min(start.day, days_in_month(year, month))
    return date(year, month, day)


def naive_next_renewal(previous_renewal: date) -> date:
    """Original code: next renewal = previous renewal + 1 month.

    Once a short month clamps the day (31 -> 28), the lost days never come
    back, because the clamped date is the input for every later renewal.
    """
    return add_months_clamped(previous_renewal, 1)


def naive_schedule(signup: date, count: int) -> list[date]:
    """The first ``count`` renewals produced by chaining naive_next_renewal."""
    renewals = []
    current = signup
    for _ in range(count):
        current = naive_next_renewal(current)
        renewals.append(current)
    return renewals


def renewal_date(signup: date, cycle_number: int) -> date:
    """Date of the ``cycle_number``-th renewal (1 = first renewal).

    Always computed from the signup date, so the billing day is only clamped
    in months that are too short and returns to it afterwards.
    """
    if cycle_number < 1:
        raise ValueError("cycle_number starts at 1")
    return add_months_clamped(signup, cycle_number)


def schedule(signup: date, count: int) -> list[date]:
    """The first ``count`` renewal dates, each anchored to the signup day."""
    renewals = []
    for cycle_number in range(1, count + 1):
        renewals.append(renewal_date(signup, cycle_number))
    return renewals


def next_renewal_after(signup: date, today: date) -> date:
    """First renewal strictly after ``today``.

    Used by the billing job instead of "stored date + 1 month", so a renewal
    can be recomputed at any time without knowing how earlier ones clamped.
    """
    # Start from the calendar months between signup and today. The renewal one
    # cycle earlier always falls in an earlier month than today, so starting
    # here never skips a renewal; step forward until we pass today.
    months_elapsed = (today.year - signup.year) * MONTHS_PER_YEAR + (today.month - signup.month)
    cycle_number = max(1, months_elapsed)
    while renewal_date(signup, cycle_number) <= today:
        cycle_number += 1
    return renewal_date(signup, cycle_number)


def days_lost_in_first_year(signup: date) -> int:
    """Total days by which the naive schedule falls behind the anchored one."""
    naive = naive_schedule(signup, MONTHS_PER_YEAR)
    fixed = schedule(signup, MONTHS_PER_YEAR)
    total = 0
    for naive_date, fixed_date in zip(naive, fixed):
        total += (fixed_date - naive_date).days
    return total
