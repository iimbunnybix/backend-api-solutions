# Backend & API Engineering Solutions

Real production problems in backend services and APIs (retries, rate limits, caching, concurrency, databases), each with root cause, a working Python fix and tests.

Every problem folder has the same structure:

| File | Contents |
|---|---|
| `README.md` | The incident or requirement, root cause, solution and trade-offs |
| `solution.py` | Working implementation (Python standard library only) |
| `test_solution.py` | Tests that reproduce the problem and prove the fix |

## Problems

<!-- INDEX:START -->
| # | Problem |
|---|---|
| 001 | [Payment API double-charges customers when the mobile app retries](problems/001-idempotent-payment-retries) |
| 002 | [One client's script floods the public API and slows it for everyone](problems/002-token-bucket-rate-limiter) |
| 003 | [Order history page gets slower the more orders a customer has, and the database CPU climbs with it](problems/003-n-plus-one-order-listing) |
| 004 | [Mobile activity feed shows the same items twice while scrolling, and some items never appear](problems/004-keyset-pagination-duplicates) |
| 005 | [Checkout API hangs for every user a few minutes after a spike in validation errors, and only a restart fixes it](problems/005-connection-pool-leak-exhaustion) |
| 006 | [Customers were charged but their orders never shipped, and nothing in the logs said why](problems/006-transactional-outbox-lost-events) |
| 007 | [Merchandisers' price changes kept reverting a few minutes after they saved them](problems/007-optimistic-locking-lost-updates) |
| 008 | [Customers saw "checkout timed out", retried, and found their card charged twice](problems/008-deadline-propagation-timeouts) |
| 009 | [Product pages went down because the optional "you may also like" widget's service was slow](problems/009-circuit-breaker-slow-dependency) |
| 010 | [Order confirmation emails stopped for six hours while the queue consumer ran at 100% CPU](problems/010-poison-message-dead-letter) |
| 011 | [Dishes the kitchen marked "sold out" kept showing as available in the app for a whole day](problems/011-cache-invalidation-stale-set-race) |
| 012 | [Finance found invoices that didn't add up and tax lines a cent short, every single day](problems/012-float-money-rounding-cents) |
| 013 | [Wallet transfers failed with "deadlock detected" every evening, and the support queue filled with "my money is stuck" tickets](problems/013-transfer-deadlock-lock-ordering) |
| 014 | [Older Android app versions crashed on the order screen right after a backend release that "only renamed a field"](problems/014-breaking-change-api-versioning) |
| 015 | [Checkout returned 503 for most shoppers because the optional recommendations service got slow](problems/015-bulkhead-shared-worker-pool) |
| 016 | [Primary database CPU spiked during a scrape of product IDs that don't exist](problems/016-negative-caching-missing-keys) |
| 017 | [Merchants re-uploaded the same 1,000-row product file a dozen times because one bad row rejected all of it](problems/017-bulk-import-all-or-nothing-rollback) |
| 018 | [API workers were OOM-killed one after another when a customer's script uploaded a huge file to a JSON endpoint](problems/018-unbounded-request-body-oom) |
<!-- INDEX:END -->

## Run the tests

```bash
python run_tests.py                       # all problems
cd problems/<folder> && python -m unittest -v
```

Requires Python 3.10+. No third-party packages.

## Author

Hrishikesh Jadhav
