# 014 · Older Android app versions crashed on the order screen right after a backend release that "only renamed a field"

## Problem
An e-commerce company's order API is used by its web app, iOS and Android
apps, and by merchants' own integrations. A backend release replaced the order
`price` string (`"12.50"`) with `amount: {value: 1250, currency: "USD"}` to
support multiple currencies, nested `customer_name` into a `customer` object,
and added a new order status, `partially_refunded`.

The web app shipped with the backend and was fine. Android users on app
versions more than a few weeks old (they update slowly, and some never do)
crashed every time they opened their order history. Two merchants' fulfilment
integrations started failing. The release was rolled back, which blocked the
multi-currency launch until the API could change without breaking anyone.

## Root cause
The API had one shape, "whatever the server returns today", and every client
was coupled to it. Removing or renaming a field is a breaking change, and so is
adding an enum value: clients that decode enums strictly (Kotlin/Swift enums,
JSON schema validation) reject a value they have never seen. Mobile apps and
third-party integrations cannot be upgraded at the same time as the server, so
"coordinate the release" was never an option.

## Solution
`solution.py` implements date-based versioning with change transformers, the
model Stripe describes for its own API:

1. **Clients pin a version.** Each request may send `Api-Version: 2026-03-01`.
   Without the header the client gets the version its API key (or app build)
   was pinned to when it was created, never "latest", so new breaking changes
   cannot reach it by accident. Unknown or malformed versions return 400 with
   the list of supported versions.
2. **One internal shape.** Handlers only produce and consume the latest
   representation. Each breaking change is a `VersionChange(date, downgrade,
   upgrade_request)`.
3. **Transform at the edge.** For an older client, the latest response is
   passed through the `downgrade` of every change newer than the client's
   version, newest first (`amount` -> `price`, `customer` -> `customer_name`,
   `partially_refunded` -> `refunded`). Request bodies are upgraded the other
   way, oldest first, so old clients can still create orders.
4. **Deprecation signal.** Versions older than `sunset_before` get
   `Deprecation` and `Link` headers, so you can see which keys still use them
   and contact their owners before removing support.

The tests reproduce the crash with a strict "old Android client" parser, then
show the same parser working against the versioned API, plus the intermediate
versions, request upgrades (exact cents, negative amounts), pinning, 400s for
bad versions and malformed or sub-cent prices, and that
downgrading never mutates the source object.

## Trade-offs
- Every downgrade must stay correct forever, or until the version is retired.
  Keep them small and tested; a long chain adds latency per request, so
  retire old versions when traffic on them is gone.
- Some changes cannot be downgraded losslessly. A EUR order cannot be shown to
  a client whose API only knows USD; the code raises instead of inventing a
  number. Decide per endpoint whether to hide such records, return an error,
  or force those clients to upgrade.
- URL versioning (`/v1`, `/v2`) is simpler to route and cache, but each major
  version tends to become a separate copy of the code, and clients have to
  migrate everything at once. Header versioning needs `Vary: Api-Version` on
  cached responses.
- Additive-only rules ("never remove, only add optional fields") avoid
  versioning for many changes but do not cover renames, type changes or new
  enum values for strict clients. Document that clients must tolerate unknown
  enum values, but do not rely on old builds doing so.
- GraphQL handles additions well via field selection but still needs
  deprecation for removals.

## Run
```bash
python -m unittest -v test_solution.py
```
