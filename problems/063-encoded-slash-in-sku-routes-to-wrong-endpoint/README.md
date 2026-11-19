# 063 · Product pages for SKUs like "TOOLS/DRILL-18V" returned 404 "store DRILL-18V not found" even though clients encoded the slash

## Problem
A marketplace's catalogue API serves `GET /v1/products/{sku}` and
`GET /v1/products/{sku}/{store_id}` (stock in one store). After onboarding a
tools supplier, about 4% of SKUs contained a slash, such as
`TOOLS/DRILL-18V`.

The mobile app did the right thing and percent-encoded the SKU:
`/v1/products/TOOLS%2FDRILL-18V`. Every one of those product pages failed
with `404 store DRILL-18V not found`, and SKUs with two slashes failed with a
plain 404. The supplier's products could not be viewed or bought, and the
error message sent engineers looking at the store service first.

## Root cause
`naive_route` called `unquote` on the **whole path** and then split it on
`/`. Decoding first turns the encoded slash back into a real one, so the
router sees `v1 / products / TOOLS / DRILL-18V`: four segments, which match
the store-stock route with `sku="TOOLS"` and `store_id="DRILL-18V"`. The
information the client gave by encoding the slash ("this is data, not a
separator") was thrown away before routing.

## Solution
`route` follows the URL rules (RFC 3986): split the **raw** path on literal
`/` first, then decode each segment separately.

- `TOOLS%2FDRILL-18V` stays one segment and becomes `sku="TOOLS/DRILL-18V"`;
  a real `/` still separates `.../TOOLS%2FDRILL-18V/store-7` into SKU and
  store.
- Each segment is decoded exactly once, so a double-encoded `%252F` arrives
  as the literal text `%2F`, not as a slash.
- `%` not followed by two hex digits (`100%`, `%ZZ`) and bytes that are not
  valid UTF-8 (`%FF`) raise `BadPathError` (a 400) instead of being passed
  through, because `unquote` would silently keep `100%` as is and make it
  look the same as the valid `100%25`.

The tests reproduce the wrong route and the 404, and check one- and
two-slash SKUs, lowercase escapes, double encoding, UTF-8 SKUs and the
rejected encodings.

## Trade-offs
- The app server is not the only layer that decodes. Apache rejects encoded
  slashes with 404 unless `AllowEncodedSlashes` is enabled, and some proxies
  and frameworks normalise the path before your code sees it. Test the full
  path through the load balancer, not just the router.
- Many teams avoid the problem instead: keep slashes out of identifiers used
  in paths (map supplier SKUs to an internal ID), or pass such values as a
  query parameter (`/v1/products?sku=TOOLS%2FDRILL-18V`), where `/` has no
  special meaning.
- Strict rejection of bad escapes can break old clients that sent sloppy
  URLs; log and measure 400s before turning it on.

## Run
```bash
python -m unittest -v test_solution.py
```
