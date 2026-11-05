# 027 · Ops dashboard users switched the order filter mid-scroll and orders silently went missing from the list

## Problem
The operations dashboard lists orders with `GET /v1/orders?status=&sort=&cursor=`
and infinite scroll. Each response carries an opaque `next_cursor`. The front
end kept the cursor in component state. When an agent changed the status
filter (for example from "All" to "Shipped") or the sort order after scrolling
a few pages, the next request sent the **new** filters with the **old**
cursor. The API returned 200 with a plausible-looking page, so nothing seemed
wrong, but newer matching orders were missing. With a large sort-value jump
the page could also repeat rows or be empty, and an empty page looked like
"no more results". Agents reported "shipped orders that don't exist", and a
nightly export script that reused a cursor across queries under-counted
shipments in a finance report.

## Root cause
The cursor held only a **position**: the last row's sort value and id. It did
not record *which query* that position belonged to. A position only means
something for the filter and sort order that produced it:

- Same sort, different filter: the position "after order 8 by `created_at`"
  is applied to the shipped-only list, so shipped orders newer than order 8
  (order 9 in the test) are never shown.
- Different sort: a cursor holding a **total** (4000 cents) is compared with
  **timestamps**, so the "next page" in the test is the first page again
  (`[10, 9, 8]`).

Problem 004 already signs its cursors, but a signature alone does not help
here. A cursor the server really issued is still valid, just for a different
query.

## Solution
- `query_fingerprint(status, sort)` hashes a canonical JSON form of
  everything that decides which rows exist and in what order. The cursor body
  is `{"fp": fingerprint, "pos": [sort_value, id]}`.
- `CursorCodec` signs `base64(body)` with HMAC-SHA256 using a key generated at
  runtime (`secrets.token_bytes(32)`). A client cannot edit the fingerprint or
  the position without breaking the signature.
- On each request the server recomputes the fingerprint from the request's
  own parameters. A mismatch raises `CursorQueryMismatch` (map it to HTTP 400)
  with a message telling the client to request the first page again without a
  cursor. A bad signature or malformed body raises `InvalidCursor`, also 400,
  and the error never becomes a 500.
- Page size is not part of the fingerprint, because changing it does not move
  the position. The test switches from 3 to 5 rows per page and continues
  correctly.
- Keyset order uses `(value, id)` so ties never skip or repeat rows. Reading
  all pages with the same query returns each order exactly once.

## Trade-offs
- The UI must reset the cursor when filters change. Before, that bug was
  silent. Now it shows up as a 400, which is the point, but clients need to
  handle it (restart from page 1).
- In this demo the signing key exists only in one process. Several API
  instances must share one key from a secret store, or cursors fail on another
  instance. Rotating the key invalidates every open cursor. Accept the
  previous key for a short overlap during rotation.
- The cursor is signed, not encrypted. Clients can base64-decode it and read
  the last sort value. If that value is sensitive, encrypt it (e.g. AES-GCM)
  or store cursor state on the server under a random id.
- Alternative: put the filters themselves in the cursor and ignore the query
  parameters when a cursor is sent (some public APIs do this). That avoids the
  error, but a client that changes filters silently keeps getting the old
  query's results, which is confusing in a different way.
- The fingerprint must include every parameter that affects the result set.
  A new filter added later without updating `query_fingerprint` brings the
  bug back. Build the fingerprint from the same parsed-parameters object the
  query uses.

## Run
```bash
python -m unittest -v test_solution.py
```
