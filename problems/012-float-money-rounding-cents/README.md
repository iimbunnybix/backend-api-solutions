# 012 · Finance found invoices that didn't add up and tax lines a cent short, every single day

## Problem
A subscription billing service generates invoices and payment plans for
about 40,000 customers a day. Finance's daily reconciliation against the
payment processor never balanced: a few dollars were always missing or extra.
Customers on a 3-month plan for $100.00 were charged $33.33 three times, and
the plan "completed" one cent short, so the account showed a $0.01 balance and
triggered a dunning email. An auditor also found tax lines that were one cent
lower than the rate implied (8.25% on $6.00 was billed as $0.49, not $0.50).
Each cent was trivial; the reconciliation exceptions, support tickets and
audit findings were not.

## Root cause
Two separate mistakes:

1. **Money stored and computed as binary floats.** Most decimal amounts have
   no exact float representation. `6.00 * 0.0825` is not 0.495 but slightly
   less, so `round(..., 2)` gives 0.49. The same happens for `1.25 * 0.18`
   (0.22 instead of 0.23) and for the classic `round(2.675, 2) == 2.67`.
2. **Every part rounded independently.** Installments and revenue splits
   were each `round(total * share, 2)`, so the parts do not sum to the total:
   `[33.33, 33.33, 33.33]` sums to 99.99. In a seeded run over 10,000 random
   totals and plan lengths (2–12 installments), **72%** of naive plans did not
   add up to the amount owed; for weighted splits over 2–5 parties it was 32%.

## Solution
`solution.py`:

- **Integer minor units.** Amounts are parsed from decimal strings into
  integer cents with `to_cents` (which refuses floats and amounts with more
  than two decimals). Sums of integers are exact.
- **`Decimal` with an explicit rounding mode** for anything multiplied by a
  rate. `tax_cents` defaults to `ROUND_HALF_UP` (what most tax rules expect)
  and supports `ROUND_HALF_EVEN` (banker's rounding) where policy calls for it.
- **Allocate, don't round.** `allocate(total, weights)` uses the
  largest-remainder method: floor each exact share, then give leftover cents to
  the parts with the largest remainders (ties to the earliest part). Parts
  always sum to the total and each is within one cent of its exact share.
  `installments(10000, 3)` gives `[3334, 3333, 3333]`.
- **One rounding step per invoice.** `build_invoice` sums line nets in cents
  and rounds tax once on the subtotal.

The tests reproduce the float errors and check the fix on 10,000 random plans
and 5,000 random weighted splits (including zero weights, refunds with
negative totals and totals smaller than the number of parts).

## Trade-offs
- **Tax per line vs per invoice** can differ by a cent; both are legal in
  different jurisdictions. Pick one, document it, and use the same rule for
  refunds and credit notes.
- **Which part gets the extra cent** is a product decision (first installment,
  last installment, largest party). Make it deterministic so re-running a
  calculation gives the same answer.
- Currencies have different minor units (JPY has none, BHD and KWD have
  three). Store the currency with the amount and look up its exponent
  (ISO 4217) instead of hard-coding 100.
- Use `NUMERIC`/`DECIMAL` columns or integer cents in the database, and
  strings in JSON; a float anywhere in the path (DB column, JSON number parsed
  as float in a client) brings the bug back.
- `Decimal` is slower than float arithmetic. For billing volumes this does
  not matter; for high-frequency pricing use integer minor units throughout.

## Run
```bash
python -m unittest -v test_solution.py
```
