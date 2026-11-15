# 055 · Bulk price update marked the wrong products as failed, and the products that really failed showed "saved"

## Problem
A retail catalogue API offers `POST /prices/bulk` so merchandising tools can
change up to a few hundred prices in one call. To make large batches faster,
the team changed the handler to update items in parallel on a thread pool.
The response format did not change: a `results` list with one
`{"status", "error"}` entry per item, and the admin tool (like most clients)
paired results with its request by position.

The next week, a merchandiser uploaded a promotion with one mistyped SKU. The
tool showed the *first* product as failed, so she retried it (it had already
been updated) and moved on. The mistyped product was shown as saved, the
promotion went live with one product at full price, and nobody noticed until
customers complained. Support found several older batches where error
messages were attached to the wrong products.

## Root cause
The parallel handler appended each result as soon as its item *finished*
(`as_completed`). An unknown SKU fails validation immediately while a real
update waits on the database, so failures finish first and move to the front
of the list. With the request `A, B, C, D` and `C` failing, the test's finish
order `C, A, D, B` produces `[error, ok, ok, ok]`: a position-based client
marks `A` as failed and `C` as saved. The handler also dropped the SKU from
each result, so clients had nothing but position to go on. Sequential code
had hidden the dependency: request order and completion order were the same.

## Solution
- `bulk_update` keeps one slot per request item and puts each outcome into
  slot `index` whenever it finishes, so result *i* always describes item *i*.
  Items still run in parallel; only the collection step changed.
- Every result echoes `index` and `sku`. `pair_by_identifier` shows how a
  client should use them, and raises if the pairing ever disagrees.
- One failing item never fails the batch: `_apply_one` turns a
  `PriceUpdateError` into an `error` result.
- If an item somehow gets no result, `_require_all_filled` raises instead of
  silently shifting later results up a position.
- The thread pool is injected as a `runner`. Tests use `ScriptedRunner` to
  fix the completion order, so the bug reproduces on every run instead of
  only when thread timing happens to line up.

## Trade-offs
- Ordered collection waits for the slowest item before responding, which the
  original also did; streaming results as they finish (NDJSON, server-sent
  events) is possible only if every record carries its index or ID.
- Echoing the index and SKU adds a few bytes per item and is an additive,
  non-breaking response change; requiring clients to use it is a separate
  migration.
- Duplicate SKUs in one batch are distinguished only by index; some APIs
  reject duplicates or accept a client-supplied `request_id` per item instead.
- Parallel updates give up batch atomicity; when all-or-nothing is required,
  use one transaction instead (see problem 017).
- `MAX_PARALLEL_UPDATES` caps threads per request; a shared pool or bulkhead
  (problem 015) is still needed so many concurrent batches cannot overload
  the database.

## Run
```bash
python -m unittest -v test_solution.py
```
