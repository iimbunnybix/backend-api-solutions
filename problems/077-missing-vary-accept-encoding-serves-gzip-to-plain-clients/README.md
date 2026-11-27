# 077 · Older thermostats failed every firmware check with "invalid JSON" while the same URL worked fine in a browser

## Problem
A smart-home company's firmware API serves a small JSON manifest at
`GET /v1/firmware/<model>/manifest`. Around 200,000 thermostats poll it a few
times a day through a CDN, alongside the mobile app, the support web console
and a partner's monitoring health checks.

After the team turned on response compression at the origin, a growing share
of older thermostats started logging "invalid JSON" on every update check and
stopped receiving firmware. The partner's health checker flapped between
green and red. Engineers opening the URL in a browser always saw a perfect
response, and requests straight to the origin worked too, so the bug looked
random. It depended only on which kind of client had filled the CDN's cache
for that URL first; whenever a browser or the app had, every plain client
behind that CDN node broke until the entry expired.

## Root cause
The origin compressed the body when the request said `Accept-Encoding: gzip`,
but did not send `Vary: Accept-Encoding`. Without `Vary`, a shared cache is
told the response depends on the URL alone, so it stored the gzip copy under
the URL and served it to everyone. The old firmware never sends
`Accept-Encoding` and has no gzip decoder; it parsed the 107 gzip bytes
(starting `1f 8b`) as UTF-8 and failed with `UnicodeDecodeError`, while the
real body is 514 bytes of JSON.

In the reproduction, a browser request followed by a plain request through
the URL-only cache gives the plain client a `Content-Encoding: gzip` body it
cannot parse. If the plain client happens to fill the cache first, everything
works, which is why the failures came and went.

## Solution
Both sides have to change:

1. **The origin** adds `Vary: Accept-Encoding` to every response whose body
   depends on that header, compressed or not. The uncompressed copy needs it
   too, or a cache could store it as "valid for everyone".
2. **The cache** (`VaryAwareCache`) learns the `Vary` list per URL and adds
   the value of each varied request header to the key. A cache that honours
   `Vary` still fails if the origin never sends it; there is a test for that.
3. **Normalisation.** Raw `Accept-Encoding` values differ by browser and
   version (`gzip, deflate, br`, `br;q=1.0, gzip;q=0.8`, ...). Keying on the
   raw text would split one URL into many entries with identical bodies. The
   origin only ever produces gzip or plain bytes, so the key uses just
   `gzip` or `identity`, honouring `q=0` refusals and `*`. In the test, six
   different spellings produce 2 cache entries and 2 origin requests.

## Trade-offs
- **Normalisation must match what the origin does.** If the origin later adds
  Brotli, `br` becomes a third variant and the normaliser has to learn it, or
  `br`-only clients will be served plain bytes (safe, just larger).
- **Many CDNs normalise `Accept-Encoding` themselves** or compress at the edge
  from an uncompressed origin copy. Letting the CDN do compression avoids the
  problem entirely and is usually the better option when available.
- **`Vary` on other headers** (`Cookie`, `User-Agent`) can fragment a cache
  into near-useless entries. Only vary on headers that really change the body,
  and normalise them the same way.
- Entries already cached under the URL alone stay wrong until they expire;
  purge the URL after deploying the fix.

## Run
```bash
python -m unittest -v test_solution.py
```
