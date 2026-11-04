# 003 · Order history page gets slower the more orders a customer has, and the database CPU climbs with it

## Problem
The `GET /orders` endpoint of a shop backend returns a page of 50 orders,
each with its customer name and line items. It was fast in development
(a local database, a handful of rows) but in production the endpoint is
the top consumer of database connections and its latency grows with the
page size. Raising `limit` to 200 for an admin export made it time out.

The database is not slow per query: every query is a primary-key or
indexed lookup. The problem is how many of them there are.

## Root cause
The classic **N+1 query** pattern. The serializer loops over the orders and
touches `order.customer.name` and `order.items`; the ORM lazy-loads each
relationship with its own `SELECT`. One page of 50 orders issues
`1 + 50 + 50 = 101` queries (the test `test_naive_issues_2n_plus_1_queries`
reproduces exactly this count). With a networked database every query pays a
round trip, so at ~1 ms per round trip that is ~100 ms of pure waiting per
request, and it holds a pooled connection for the whole time. Latency and
connection usage scale linearly with page size.

## Solution
`list_orders_batched` loads relationships **in bulk** (the "DataLoader" /
`selectinload` / `prefetch_related` pattern):

1. Fetch the page of orders (1 query).
2. Collect distinct `customer_id`s → one `WHERE id IN (...)` query.
3. Collect order ids → one `WHERE order_id IN (...)` query for all items.
4. Stitch the response in Python with dict lookups (O(rows)).

The query count is now **3 regardless of page size** (tested for limits 1,
10 and 200). Very large pages are split into bounded `IN` chunks
(`MAX_IN_PARAMS`, default 500) so we never exceed the database's bind
parameter limit; 300 orders with `chunk=100` costs 5 queries. Tests also
prove the batched response is identical to the naive one,
preserves page order, and returns `items: []` for orders with no items.

## Trade-offs
- A single `JOIN` (orders ⨝ customers ⨝ items) is also 1 query, but it
  duplicates order/customer columns once per item row and makes `LIMIT`
  apply to item rows instead of orders. Batched loading avoids both.
- Bulk loading pulls everything for the page into memory; that is fine for
  paginated APIs, not for unbounded exports (stream those instead).
- The fix only helps if it is enforced: add a query-count assertion
  (like `QueryCounter` here) to endpoint tests so the N+1 can't silently
  return when someone adds a new field to the serializer.
- Caching customer names would hide the symptom but not the round trips
  for items, and adds invalidation problems.

## Run
```bash
python -m unittest -v test_solution.py
```
