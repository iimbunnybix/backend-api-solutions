# 074 · Product pages re-downloaded the full catalog JSON on every visit after the CDN enabled compression

## Problem
A shop's mobile app revalidated cached product data with
`If-None-Match` so that unchanged products cost a tiny `304 Not Modified`
instead of the full JSON. After the platform team turned on on-the-fly gzip at
the CDN, the 304 rate on the product API dropped to zero overnight. Every app
open downloaded every product again: origin egress and CDN bandwidth bills
jumped, and on slow mobile networks product pages took seconds longer to show
fresh data. Nothing errored, so it took a cost review to notice.

## Root cause
The API sent a strong ETag such as `"v42"` and returned 304 only when the
`If-None-Match` header was **exactly** that string. A compressing proxy changes
the bytes, so it may not pass a strong ETag through unchanged; nginx (since
1.7.3) and most CDNs turn it into a weak one, `W/"v42"`. Clients then
revalidated with `If-None-Match: W/"v42"`, which never equals `"v42"`.
The exact compare also broke two other valid forms: a list of tags
(`"v41", "v42"`) and `*`.

## Solution
`is_not_modified` in `solution.py` follows RFC 9110 section 13.1.2:

1. `*` matches any existing resource.
2. Otherwise the header is a comma-separated list of entity tags; each item is
   parsed as `"value"` or `W/"value"`, and malformed items are ignored so they
   can never produce a false 304.
3. Each tag is compared with the **weak comparison** (the `W/` prefix is
   ignored, the quoted value must match exactly, case included), which is the
   comparison the RFC requires for `If-None-Match`.

In the tests, a CDN-weakened tag now gets a 304 with an empty body, a list
matches if any item matches, and old or unquoted tags still get the full
response.

## Trade-offs
- Weak comparison is correct for `If-None-Match` on GET, but **not** for
  `If-Match` on writes: that header needs the strong comparison, so a weak tag
  must never authorise an update. Keep the two checks separate.
- Treating a weakened tag as a match assumes the uncompressed and compressed
  bodies are equivalent representations, which is what "weak" means. If the
  server sends different content per `Accept-Encoding`, it also needs
  `Vary: Accept-Encoding` so caches keep them apart.
- An alternative is to configure the CDN to keep strong ETags (some let you
  compress at the origin instead), but the server should still accept the
  forms clients are allowed to send.

## Run
```bash
python -m unittest -v test_solution.py
```
