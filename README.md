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
<!-- INDEX:END -->

## Run the tests

```bash
python run_tests.py                       # all problems
cd problems/<folder> && python -m unittest -v
```

Requires Python 3.10+. No third-party packages.

## Author

Hrishikesh Jadhav
