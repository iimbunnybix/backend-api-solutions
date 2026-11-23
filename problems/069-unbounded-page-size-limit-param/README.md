# 069 · One partner call with `?limit=500000` pinned the orders database at 100% CPU and timed out every other request

## Problem
The orders API serves `GET /orders?limit=&offset=` to the web app (which
asks for 50 at a time) and to partner integrations. One partner wanted
"all orders since launch" and, instead of paging, sent `limit=500000`.

Each such call read the whole orders table, built a response of several
hundred megabytes and held a database connection for most of a minute. The
partner's job retried on timeout, so a few calls overlapped, the database
CPU stayed at 100%, and checkout requests queued behind them and timed out.
A second partner later sent `limit=-1` "to mean no limit" and got the same
effect. Neither call was malicious, and neither was rejected.

## Root cause
The handler converted `limit` with `int()` and used it as-is
(`naive_list_orders`). Nothing bounded it:

- `limit=500000` on a 100,000-order test table reads and returns all
  **100,000** rows.
- `limit=-1` becomes the slice `[:-1]` (or `LIMIT -1`, which SQLite and some
  ORMs also treat as "no limit") and returns **99,999** rows.
- `limit=all` raises `ValueError`, which surfaced as a 500 instead of a
  client error.

The page size is a cost the *server* pays, but the client chose it.

## Solution
`list_orders` parses pagination parameters strictly and bounds the cost of
one request:

1. **Strict parsing** (`parse_positive_int`): only ASCII digits are
   accepted, so `-1`, `1.5`, `" 10"`, `""` and full-width digits get a
   **400** with a message rather than a 500 or a surprising slice.
2. **At least 1, at most `MAX_PAGE_SIZE` (200)** (`parse_limit`): `0` is
   rejected; anything larger is clamped, so old clients that ask for
   too much still work, one page at a time. A missing limit uses the
   default of 50.
3. **Echo and continue**: the response includes the `limit` actually used
   and `next_offset` (or `null` at the end), so a client that asked for
   500,000 can see it got 200 and how to get the rest. Following
   `next_offset` returns every order exactly once.

## Trade-offs
- Clamping hides the change from clients that ignore the echoed `limit`
  and assume one call returned everything. Rejecting over-large limits with
  400 is stricter and makes the mistake visible; clamping was chosen so
  existing integrations keep working while they are contacted.
- When the last page is exactly full, the client makes one extra request
  that returns an empty page. Fetching `limit + 1` rows and returning only
  `limit` avoids that.
- Offset paging still makes deep pages expensive (the database skips
  `offset` rows) and can repeat rows when data changes; see problem 004 for
  keyset pagination. This problem is only about bounding one request.
- A cap per request does not stop a client from making many requests; that
  is what rate limiting (problem 002) is for. For "give me everything", an
  asynchronous export job is a better contract than a huge page.

## Run
```bash
python -m unittest -v test_solution.py
```
