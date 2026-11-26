# 075 · Partner order exports were missing the newest orders, and small shops got empty exports

## Problem
A commerce platform exposes `GET /orders?page=N&page_size=100` to accounting
partners. Each response carries `total_items` and `total_pages`. The
partners' nightly sync reads page 1, then loops
`for page in 1..total_pages` and writes every order into the bookkeeping
system.

Merchants began reporting that their books never matched the dashboard: a
shop with 1,234 orders saw only 1,200 in its accounting tool, and the missing
34 were always the most recent ones. Shops with fewer than 100 orders, mostly
new sign-ups in their first month, got an empty export every night. Support
spent weeks blaming the partners' importer before anyone looked at the
response metadata.

## Root cause
The API computed `total_pages = total_items // page_size`. Floor division
drops the last, partly filled page: 1,234 orders at 100 per page gives
**12** pages instead of 13, so page 13 (orders 1,201-1,234) was never
requested. With 60 orders the result is **0**, and the loop never runs. The
items endpoint itself was correct, which is why every spot check of a single
page looked fine.

## Solution
- `total_pages` rounds up with integer ceiling division,
  `(total + size - 1) // size`. It stays exact for any count. The common
  alternative, `math.ceil(total / size)`, goes through a float and loses
  precision above 2**53 (for example `math.ceil((2**53 + 1) / 2)` returns
  2**52 instead of 2**52 + 1).
- Zero orders gives zero pages, an exact multiple adds no empty page, and a
  page size below 1 or above the maximum is rejected with 400 instead of
  dividing by zero.
- Each page also returns `has_next`, computed from the data
  (`start + page_size < total`), so clients can loop on it instead of doing
  the division themselves.

With the fix, the 1,234-order export returns all 1,234 orders and the 60-order
shop gets all 60, whether the client loops on `total_pages` or `has_next`.

## Trade-offs
- `total_pages` needs a `COUNT(*)`, which is expensive on large tables and
  can change between page requests while new orders arrive. Many APIs drop
  it and return only `has_next` or a cursor (see problem 004, keyset
  pagination), which is also stable under inserts.
- Clients already deployed against the broken metadata may have added their
  own `+ 1`. After the fix they request one extra, empty page: harmless, but
  worth a changelog note.
- Offset paging itself still skips or repeats rows when orders are inserted
  or deleted during an export. Rounding fixes the count, not that.

## Run
```bash
python -m unittest -v test_solution.py
```
