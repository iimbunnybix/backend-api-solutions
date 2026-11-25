# 073 · Orders placed from the new app showed "order placed" but never reached the database

## Problem
A food-ordering API normalised URLs: any request to a path without its
trailing slash, such as `POST /v1/orders`, was answered with
`301 Moved Permanently` to `/v1/orders/`. A new release of the mobile app built
the order URL without the slash. The app's HTTP client followed the redirect,
got `200 OK`, and showed "order placed". Nothing was created: for most of a
Friday evening every order from the new release was lost, and support only
found out when customers asked where their food was.

## Root cause
For historical reasons, browsers and most HTTP libraries (`requests`,
`urllib`, OkHttp, browsers' `fetch`) follow a `301` or `302` for a POST by
sending a **GET without the body** (RFC 9110 section 15.4.2/15.4.3 allows this;
`303` requires it). The replayed `GET /v1/orders/` hit the order *listing*
endpoint, which returned `200`, so the failure was silent on both sides: no
error on the server and a success status on the client.

## Solution
`redirect_status` in `solution.py` keeps `301` only for `GET` and `HEAD`
(caches and crawlers understand it) and answers every other method with
`308 Permanent Redirect`. RFC 9110 forbids clients from changing the method or
dropping the body on `307`/`308`, so the order is replayed intact and created
exactly once. The helpers `follow_up_request` and `client_send` follow redirects the way common clients do, so the tests show
the order being lost with `301` and created with `308`.

## Trade-offs
- An even safer option for write endpoints is not to redirect at all: route
  both `/v1/orders` and `/v1/orders/` to the same handler. A redirect costs an
  extra round-trip and the request body is uploaded twice.
- Some very old clients and proxies do not understand `308` (it was
  standardised in 2015, RFC 7538); `307` is older and has the same
  "keep the method" rule but is temporary, so caches will not remember it.
- Clients may refuse to re-send a body to a different host for security
  reasons; this fix assumes the redirect stays on the same origin.
- The endpoint returning `200` to the bodyless GET hid the bug. Monitoring the
  ratio of POSTs to created orders, or alerting on 3xx responses to non-GET
  requests, catches this class of problem early.

## Run
```bash
python -m unittest -v test_solution.py
```
