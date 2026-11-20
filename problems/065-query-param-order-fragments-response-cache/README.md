# 065 · The product-listing cache hit rate stayed below 50% during a sale, and the database fell over serving the same 20 pages

## Problem
A shop's `GET /products` listing endpoint sits behind a response cache (the
same happens with a CDN). Responses are cached for a few minutes under the
request path plus query string. During a sale, almost all traffic went to
the first few pages of one category in a handful of sort orders: about 20
distinct listings.

The cache dashboard still showed a hit rate below 50%, the database CPU sat
at 100%, listing pages timed out and the sale banner sent shoppers to error
pages. The cache had plenty of memory free; it was full of near-identical
entries.

## Root cause
The cache key was the query string **exactly as the client sent it**
(`naive_cache_key`). The web app, the iOS app, the Android app and marketing
links all spelled the same request differently:

- parameter order: `?page=2&sort=price` versus `?sort=price&page=2`,
- percent-encoding: `brand=acme%2Czen`, `acme%2czen` and `acme,zen`;
  `q=red+kettle` and `q=red%20kettle`,
- empty segments: a trailing `&` or `&&` left by string concatenation,
- tracking parameters: every `utm_source`, `utm_campaign`, `gclid` or
  `fbclid` value made a new entry, although the API never reads them.

Each spelling was a separate entry, so each one went to the database.
`ReproduceProblem` replays 10,000 seeded requests for 20 listings, written
in these mixed styles: the raw key creates 5,812 entries and 5,812 database
calls, a 41.9% hit rate.

## Solution
`canonical_cache_key` builds the key from the *meaning* of the query, not
its spelling:

1. **Parse** with `parse_qsl(keep_blank_values=True)`. This decodes every
   percent-encoding to the same text, turns `+` into a space, and skips
   empty segments.
2. **Drop tracking parameters**: anything starting with `utm_`, plus
   `fbclid`, `gclid`, `msclkid` and `_ga`.
3. **Sort by name only.** Python's sort is stable, so repeated values keep
   their relative order: `sort=rating&sort=price` (rating, then price as
   tie-breaker) stays different from `sort=price&sort=rating`.
4. **Re-encode** every name and value with one rule: only RFC 3986
   unreserved characters are left as is, everything else becomes
   upper-case `%XX`.

On the same 10,000 requests the canonical key creates 20 entries, one per
real listing, and 20 database calls: a 99.8% hit rate. The tests also check
that different values, different paths, swapped repeated values, a blank
value (`brand=`) and an encoded `&` inside a value all keep separate keys.

## Trade-offs
- The ignore list must match what the handler really ignores. If an
  endpoint starts reading a parameter that is on the list (or one that is
  not, like a new `currency`), the key has to change with it, or users get
  each other's responses (see problem 019). Generating the key from the
  same schema the handler parses is the safest option.
- Sorting assumes the API does not care about the order of *different*
  parameter names. That is true for almost every API; one that does care
  needs its own key rule.
- `brand=` and a missing `brand` are kept apart because a handler may treat
  them differently. If yours treats them the same, drop blank values too
  for a few more hits.
- A CDN usually needs this configured instead of coded: query-string
  sorting and an allowlist or blocklist of cache-key parameters (CloudFront
  cache policies, Fastly `querystring.sort`, Cloudflare cache keys).
  Normalising in the clients helps, but the server cannot rely on it.

## Run
```bash
python -m unittest -v test_solution.py
```
