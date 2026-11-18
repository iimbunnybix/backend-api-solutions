# 061 · Customers who signed up on the 31st were billed on the 28th of every month after February, and finance saw revenue slide earlier each month

## Problem
A subscription service bills monthly plans. A nightly job finds subscriptions
whose `next_renewal` is today, charges them, and stores
`next_renewal = next_renewal + 1 month`.

Support started getting "why was I charged early?" tickets from customers who
had signed up on the 29th, 30th or 31st. Their first renewal in February was
correctly clamped to the 28th, but every renewal after that also happened on
the 28th. Over the first year a customer who signed up on 31 January 2025 was
charged a total of 29 days earlier than their billing day, and the January 2026
renewal came on the 28th instead of the 31st. Some card issuers flagged the
"early" charges, and finance's month-end revenue was skewed because a slice of
renewals moved from the last days of the month into the previous week.

## Root cause
`naive_next_renewal` computes the next date from the **previous renewal**,
not from the signup date. Clamping is lossy: 31 January + 1 month has no
31 February, so it becomes 28 February, and the information "this customer's
day is the 31st" is gone. Every later renewal is "28th + 1 month" = the 28th.
Customers who signed up on the 1st to the 28th are never clamped, which is why
the bug looked like it affected only a few accounts.

## Solution
- Keep the **anchor**: the signup date (or the billing day the customer
  chose). `renewal_date(signup, n)` adds `n` months to the anchor in one step
  and clamps only for the month being computed, so after February the day goes
  back to 31 (or 30 in 30-day months).
- `add_months_clamped` does the month arithmetic with a zero-based month count,
  so December rolls into January of the next year, and uses
  `calendar.monthrange` for month lengths, so leap years are handled
  (31 January 2024 renews on 29 February 2024).
- `next_renewal_after(signup, today)` lets the billing job recompute the next
  date from the anchor at any time instead of chaining from a stored date. A
  test checks it against the full schedule for every day of two years.

For a 31 January 2025 signup the naive schedule loses 29 days in the first
twelve renewals; the anchored schedule loses none, and mid-month signups get
exactly the same dates as before.

## Trade-offs
- The anchor must be stored. If only `next_renewal` exists in the database,
  add a `billing_day` column and backfill it from the signup date; customers
  already drifted to the 28th need a decision (move them back, which shortens
  one cycle, or keep the 28th).
- Some businesses prefer "31st → 1st of next month" (roll forward) instead of
  clamping. That is a product decision; whichever rule is chosen, apply it to
  the anchor, not to the previous result.
- Plan changes, pauses and proration usually reset the anchor. Make that an
  explicit event rather than a side effect of the renewal job.
- This uses calendar dates only; the job's time zone still decides which
  "today" a renewal belongs to (see problem 022 for time-zone pitfalls).

## Run
```bash
python -m unittest -v test_solution.py
```
