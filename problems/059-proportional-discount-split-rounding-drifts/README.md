# 059 · Finance found refunds and invoices off by a few paise on every order that used a "₹100 off" coupon

## Problem
An e-commerce checkout service supports flat coupons such as "₹100 off your
order". GST invoices and partial refunds need the discount per line item, so
the service spread the coupon across the items in proportion to their price
and stored one discount figure per line.

Reconciliation started failing every night. On a 3-item order of ₹499 items
the per-item discounts added up to ₹99.99, so the invoice total disagreed with
the payment by one paisa. On a 6-item order they added up to ₹100.02, and when
a customer returned everything the refund was larger than what they had paid.
Across order sizes 1 to 50 (all ₹499 items) the per-line shares failed to add
up to the coupon for 39 of the 50 sizes. Each mismatch is tiny, but at
hundreds of thousands of orders a day it produced thousands of reconciliation
breaks, a growing pile of manual journal entries and over-refunds that could
be repeated on purpose.

## Root cause
Each share was computed and rounded on its own:

```python
shares[sku] = round(discount_paise * price / order_total)
```

Rounding errors of up to half a paisa per line do not cancel out. Three
shares of 3333.33 paise each round down to 3333 (total 9999); six shares of
1666.67 each round up to 1667 (total 10002). Nothing forces the rounded parts
to add back up to the whole.

## Solution
`split_discount` uses the **largest-remainder method** on integer paise:

1. For each line compute `divmod(discount * price, order_total)`: the
   rounded-down share and the exact remainder. All integers, no floats.
2. The shares are now short by `leftover` paise, which is always fewer than the
   number of lines.
3. Give one extra paisa to each of the `leftover` lines with the biggest
   remainders; ties go to the earlier line so regenerating an invoice gives the
   same split.

The result always sums exactly to the coupon, every share is within one paisa
of its exact proportional value, and returning every item refunds exactly what
the customer paid. For a ₹19,999 phone, ₹499 case and ₹299 cable, the ₹100
coupon splits as ₹96.16, ₹2.40 and ₹1.44. Invalid input (empty order,
duplicate SKUs, negative prices, a discount larger than the order) is
rejected instead of producing a strange split.

## Trade-offs
- Which line receives the extra paisa is a policy choice; finance must agree
  on it, and it has to stay stable once invoices are issued (store the shares,
  do not recompute them on refund).
- Partial refunds use the stored per-line share, so the last item returned is
  not "topped up" to match a re-split of the remaining items. This keeps a
  refund for an item identical no matter what order items are returned in.
- Percentage coupons, quantities and per-line tax would each need the same
  treatment; a real system splits at the unit level and allocates tax after
  the discount.
- Alternatives: give all rounding drift to the most expensive line (simpler,
  less fair), or keep shares in higher precision and round only at payout
  (moves the problem rather than solving it).

## Run
```bash
python -m unittest -v test_solution.py
```
