# 007 · Merchandisers' price changes kept reverting a few minutes after they saved them

## Problem
The catalog admin API serves ~40k products. Merchandisers edit products in a
web form that loads the record with `GET /products/{id}` and saves it with a
full `PUT`. A stock-sync job does the same read-modify-write every few minutes
to update warehouse counts. During a weekend sale, dozens of price cuts
"reverted" shortly after being saved: the page showed the new price, then the
old one came back. Customers saw full prices on sale items for hours, and
marketing re-entered prices by hand, only for some to revert again.

## Root cause
A **lost update**. Both writers read the same version of the row, changed
different fields, and sent the *whole* record back. The handler wrote all
fields unconditionally, so whoever saved last overwrote the other writer's
change with the stale value it had read. Nothing failed and nothing was logged.
`ReproduceLostUpdate` shows Alice's price cut wiped out by Bob's stock update.

Database transactions alone do not help: the race spans two HTTP requests
(the GET and the later PUT), and no transaction is open in between.

## Solution
**Optimistic concurrency control with ETags.**

1. Each row has a `version` column. `GET` returns it as `ETag: "v<version>"`.
2. `PUT` must send `If-Match: <etag>`. The write is a single conditional
   statement, `UPDATE ... SET ..., version = version + 1 WHERE id = ? AND version = ?`,
   so the check and the write are atomic in the database.
3. If zero rows match, the record changed since the client read it: return
   **412 Precondition Failed** with the current ETag, and the UI asks the user
   to reload and re-apply. A missing `If-Match` returns **428 Precondition
   Required**, so old clients cannot silently bypass the check. A malformed
   ETag is treated as a mismatch.
4. Machine writers use `update_with_retry(change)`: re-read, re-apply a pure
   function, conditional write, repeat on 412. In the test, 8 threads × 5 stock
   decrements leave stock at exactly 0 and the version at 41 (one bump per
   write), with no decrement lost.

No locks are held between requests, so readers never block and an abandoned
edit form costs nothing.

## Trade-offs
- Under heavy contention on one row, optimistic writers keep retrying; a
  bounded retry count turns that into an error (`ConflictRetriesExhausted`)
  instead of a livelock. For true hot counters, use an atomic
  `UPDATE ... SET stock = stock - 1 WHERE stock > 0` or a sharded counter.
- Humans resolving a 412 is friction. Field-level `PATCH` reduces conflicts
  (two writers touching different fields could both succeed), but needs merge
  rules and still needs a version check for same-field edits.
- Pessimistic locking (`SELECT ... FOR UPDATE`) avoids retries but holds locks
  while a user thinks, and cannot span HTTP requests safely.
- The version must change on *every* write path, including batch jobs and
  direct SQL; one path that skips it brings the bug back.

## Run
```bash
python -m unittest -v test_solution.py
```
