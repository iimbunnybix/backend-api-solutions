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
| 019 | [Shoppers on one store saw another store's prices after a "harmless" response-caching release](problems/019-response-cache-key-missing-tenant) |
| 020 | [Support found customers with two accounts, and a user registered "Admin" right next to "admin"](problems/020-case-unicode-duplicate-accounts) |
| 021 | [Customers clicking an order in the web app saw someone else's order, but the mobile app was fine](problems/021-json-large-id-precision-loss) |
| 022 | [Patients in India arrived five and a half hours early for video consultations booked through the app](problems/022-naive-datetime-timezone-shift) |
| 023 | [Database CPU spikes to 100% exactly five minutes after every deploy, then again every five minutes](problems/023-synchronized-cache-expiry-ttl-jitter) |
| 024 | [Users who changed only their display name in the app found their phone number and bio erased](problems/024-partial-update-null-vs-missing) |
| 025 | [Support agents searching for "_" got every customer in the database, and "bob_smith" also returned "bobXsmith"](problems/025-like-wildcard-search-escaping) |
| 026 | [Push notifications from customers with Hindi names or emoji in their message silently stopped arriving](problems/026-utf8-byte-truncation-split-characters) |
| 027 | [Ops dashboard users switched the order filter mid-scroll and orders silently went missing from the list](problems/027-pagination-cursor-not-bound-to-filters) |
| 028 | [After one member checked out, every shopper saw the member price, and it dropped further with each new member](problems/028-shared-cache-object-mutation) |
| 029 | [Every API endpoint returned "connection pool exhausted" whenever the payment provider got slow](problems/029-transaction-held-across-payment-call) |
| 030 | ["My orders" showed only an error for every customer who had used the new click-and-collect option](problems/030-closed-enum-new-value-client-crash) |
| 031 | [Support console returned HTTP 500 whenever an agent selected more than a thousand customers](problems/031-sql-in-list-bind-parameter-limit) |
| 032 | [Big merchants' "Export orders" button always ended in a 504, and every retry made the database slower](problems/032-long-export-load-balancer-timeout) |
| 033 | [New users tapping "Continue with Google" got "Something went wrong" on their very first login](problems/033-get-or-create-signup-race) |
| 034 | [Paid orders vanished during deploys, with no error in any log and an empty queue](problems/034-ack-before-processing-lost-orders) |
| 035 | [Users who deleted their account got "email already registered" when they tried to come back](problems/035-soft-delete-unique-email-resignup) |
<!-- INDEX:END -->

## Run the tests

```bash
python run_tests.py                       # all problems
cd problems/<folder> && python -m unittest -v
```

Requires Python 3.10+. No third-party packages.

## Author

Hrishikesh Jadhav
