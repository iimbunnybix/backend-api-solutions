# 039 · Product pages were re-downloaded in full on every visit even though nothing had changed

## Problem
A retail catalog API serves product JSON to the mobile app and through a CDN.
Responses carry an `ETag`, and clients revalidate with `If-None-Match` so an
unchanged product costs a `304 Not Modified` with no body. After the catalog
was moved from one large server to twelve smaller ones behind a load
balancer, the CDN's revalidation hit rate fell below 10% (a request that
lands on a random one of 12 servers matches about 1 time in 12), origin
egress jumped, and app users on slow networks waited for full
product payloads on every screen. No product had changed: the same product
simply came back with a different ETag depending on which server answered.

## Root cause
The ETag was `sha256(json.dumps(body))`, and the bytes of `json.dumps` were
not a function of the content alone:

- `tags` is a Python `set` of strings. String hashing is randomised per
  process (`PYTHONHASHSEED`), so each server iterates the set in its own
  order. In the tests, the same six-tag product hashed on 12 server processes
  (hash seeds 1-12) gives **12 different ETags**; across 100 seeds, 92.
- `attributes` is a dict filled in whatever order its upstream sources
  answered. Equal dicts with different insertion order serialise differently.

An ETag must change when the representation changes and stay the same when it
does not. Here it changed with the server and with timing, so `If-None-Match`
almost never matched.

## Solution
`solution.py` keeps `naive_serialize` / `naive_respond` for comparison and
adds a canonical path:

1. `canonical_serialize` rebuilds the body so its JSON does not depend on
   insertion order: object keys are sorted, sets become lists sorted by each
   element's canonical JSON (so mixed types such as `{2, "1", None}` work),
   list order is kept because in a list order is content, separators are
   fixed and `NaN` is rejected instead of emitting invalid JSON.
2. `respond` hashes **the exact bytes it sends**, so the ETag and the body
   can never disagree, and returns `304` with an empty body when the client's
   tag is current.
3. `if_none_match_hits` follows RFC 9110: `*`, comma-separated lists, and the
   weak comparison that `If-None-Match` uses (`W/"x"` matches `"x"`).

The tests run the ETag computation in real subprocesses with different
`PYTHONHASHSEED` values to show the naive ETag differs across servers and the
canonical one does not.

## Trade-offs
- Canonicalising costs an extra walk over the body and sorting of sets; for
  large list endpoints, prefer an ETag derived from data versions (for
  example `max(updated_at)` plus row count, or a version column) so you do
  not have to serialise just to answer a `304`.
- Hashing the body still requires building the full response on the origin;
  it saves bandwidth, not database work.
- Setting `PYTHONHASHSEED=0` on every server would hide the set problem but
  not the insertion-order one, and it weakens protection against hash-flooding
  attacks on dicts. Fix the serialisation instead.
- Responses that legitimately differ per user or per `Accept-Language` need
  those inputs reflected in the cache key (`Vary`), not only in the ETag.
- If a proxy re-compresses or rewrites the body, it should mark the tag weak;
  `If-Match` on writes must use the strong comparison (see problem 007).

## Run
```bash
python -m unittest -v test_solution.py
```
