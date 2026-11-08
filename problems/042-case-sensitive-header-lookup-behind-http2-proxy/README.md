# 042 · After an HTTP/2 proxy went live, checkout failed with "415 Unsupported Media Type" and retried payments were charged twice

## Problem
A subscription company's payment API is called by its web and mobile apps.
The apps send `Content-Type: application/json` and an `Idempotency-Key` per
checkout, and retry on timeouts. The platform team then put an HTTP/2-capable
proxy (an Envoy-based ingress) in front of the service to get connection
multiplexing for mobile clients. Load tests passed, because they talked to the
service directly over HTTP/1.1.

After the switch, part of the traffic started failing with `415 Unsupported
Media Type` for perfectly valid JSON. On the renewal endpoint, where the
media-type check had been relaxed long ago for an old client, requests went
through, and the next day support saw customers charged twice for one
subscription: every retry after a timeout created a new charge. Finance
had to refund duplicates by hand and the rollout was reverted.

## Root cause
**The service looked headers up case-sensitively in a plain dict.** The
handler did `headers = dict(raw_headers)` and then
`headers.get("Content-Type")` and `headers.get("Idempotency-Key")`.

HTTP field names are case-insensitive (RFC 9110), and HTTP/2 and HTTP/3
require them to be sent in lowercase. The proxy forwarded `content-type` and
`idempotency-key`, so:

- `headers.get("Content-Type")` returned `None`, so the service answered 415;
- `headers.get("Idempotency-Key")` returned `None`, and because the key was
  treated as optional, every retry was charged as a new payment.

The dict also silently dropped repeated field lines: `dict()` of two `Accept`
lines keeps only the last one, and two different `Idempotency-Key` lines
would let whichever came last win.

The reproduction runs the same requests with and without a fake HTTP/2 proxy
that lowercases names: direct requests work, proxied ones get 415, and a
request retried with a lowercased key charges twice.

## Solution
`solution.py` keeps `naive_handle_payment` and adds a `Headers` multi-map used
by `handle_payment`:

- **Case-insensitive lookups.** Values are indexed by `name.lower()`, so
  `Content-Type`, `content-type` and `CONTENT-TYPE` are the same header. The
  original spelling is kept in `lines()` for logging and forwarding.
- **Every field line is kept.** `get_all()` returns one value per line, in
  order. Use it for `Set-Cookie`, whose values contain commas and must never
  be joined. `get_list()` splits comma-separated list headers (`Accept`,
  `Via`, `X-Forwarded-For`) across all their lines.
- **Strict single values.** `get_one()` returns the value of a header that may
  appear once, accepts identical duplicates, and raises `DuplicateHeader` when
  the lines disagree. The handler turns that into a 400, so a client or proxy
  cannot pick which `Idempotency-Key` the service sees.
- **Media type parsing.** `media_type()` compares only the type, ignoring case
  and parameters, so `Application/JSON; charset=utf-8` is JSON.
- **No silent fallback.** A payment without an idempotency key is rejected with
  400 instead of being charged without deduplication.

## Trade-offs
- Rejecting requests without an idempotency key breaks clients that never
  sent one. Roll it out with a metric and a warning period first.
- Rejecting conflicting duplicates is stricter than many frameworks, which
  keep the first or last value. That is deliberate for security-relevant
  single-value headers (`Content-Type`, `Content-Length`, `Host`, auth and
  idempotency headers), where proxies and servers choosing differently is how
  request-smuggling and cache-poisoning bugs start.
- `get_list()` splits on every comma. That is right for list headers but
  wrong for headers whose values contain quoted commas or dates (`Set-Cookie`,
  `Expires`); use `get_all()` for those.
- Most frameworks already provide this (`email.message.Message` /
  `http.client.HTTPMessage`, Starlette and Werkzeug `Headers`, Go
  `http.Header`). The bug usually appears where code copies headers into a
  plain dict, as here, so search for `dict(request.headers)` and string-keyed
  header lookups in middleware and logging.
- Test through the same proxy chain as production: tests that bypass the
  proxy never see lowercased names.

## Run
```bash
python -m unittest -v test_solution.py
```
