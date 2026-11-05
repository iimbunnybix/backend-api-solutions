# 018 · API workers were OOM-killed one after another when a customer's script uploaded a huge file to a JSON endpoint

## Problem
A document-metadata API runs as a pool of workers, each with a 512 MiB memory
limit. Normal request bodies are a few kilobytes of JSON. One afternoon a
customer's sync script started posting whole archive files (several gigabytes)
to `POST /documents/metadata` by mistake. Each worker that picked up one of
those requests grew until the kernel OOM-killed it, dropping every other
request it was serving. Retries spread the large uploads across the pool and
error rates stayed high for about 40 minutes until the client was blocked.

In the simulation in `solution.py` a 64 MiB upload makes the original reader
pull **all 67,108,864 bytes** into memory. The fixed reader rejects it after
reading **0 bytes** when Content-Length is sent, and after **1,114,112 bytes**
(the 1 MiB limit plus one 64 KiB chunk) when the body is chunked.

## Root cause
The handler did `body = stream.read()`: read everything the client sends,
then parse it. Nothing limited the size, so the client, not the server,
decided how much memory each request used. Checking `len(body)` afterwards
does not help because the memory is already spent.

## Solution
`read_body()` bounds memory for every request:

1. **Validate Content-Length** strictly (digits only). Values like `-1`,
   `+5` or `abc` get **400**.
2. If the declared length is over `MAX_BODY_BYTES`, return **413 Payload Too
   Large** straight away, without reading any of the body.
3. If the length is acceptable, read **exactly** that many bytes in 64 KiB
   chunks; a body that ends early is a 400.
4. If there is no Content-Length (chunked transfer), read 64 KiB chunks and
   stop with 413 as soon as the running total passes the limit.

The handler maps the rejection to an HTTP status, so the client gets a clear
error instead of a dropped connection.

## Trade-offs
- The limit has to fit the largest legitimate request. Endpoints that really
  accept files should stream them to disk or object storage (or use
  pre-signed upload URLs) instead of raising the global limit.
- Set the same limit earlier as well: reverse proxies and load balancers
  (for example nginx `client_max_body_size`) can reject huge bodies before
  they reach the application at all.
- After a 413 the server usually closes the connection, because the unread
  body is still on the socket and cannot be skipped cheaply.
- This bounds memory, not time: a client that sends one byte per second
  needs a read timeout as well.

## Run
```bash
python -m unittest -v test_solution.py
```
