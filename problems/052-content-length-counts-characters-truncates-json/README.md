# 052 · Product pages for "Zoë" and prices in ₹ failed with "Unexpected end of JSON input", while English pages worked

## Problem
A lightweight catalogue service wrote its HTTP/1.1 responses by hand on a
keep-alive socket server. After the catalogue launched in India and Europe,
mobile clients started reporting `Unexpected end of JSON input` on some
product pages. Only products with non-ASCII text failed: the body
`{"name": "Zoë", "price": "₹499"}` is **32 characters but 35 bytes** in
UTF-8. Worse, on reused connections the next request sometimes failed too,
with a client error about a malformed status line.

## Root cause
The writer set `Content-Length: len(body)` on a Python `str`, which counts
**characters**. HTTP's Content-Length counts **bytes** of the encoded body.
`ë` is 2 bytes and `₹` is 3 bytes in UTF-8, so the header said 32 while 35
bytes were sent. The client read 32 bytes (`...₹49`), so the JSON was cut
off, and the 3 leftover bytes `9"}` stayed on the connection. The client then
read `9"}HTTP/1.1 200 OK` as the next status line and failed that response
too. ASCII-only bodies hid the bug because their character and byte counts
are equal.

## Solution
`build_response` encodes the body to bytes once, sets Content-Length from
`len(body_bytes)`, and states `charset=utf-8` in Content-Type so the client
decodes with the same encoding used for counting. The test client
(`read_responses`) reads exactly Content-Length bytes per response and
validates the status line, so it detects both the truncated body and the
desynchronised connection with the original writer.

## Trade-offs
- The simplest fix is not to hand-write HTTP at all: frameworks and
  `http.server` compute the length from the bytes they actually send.
- Encoding the whole body to compute its length needs the full body in
  memory. For large or streamed bodies use `Transfer-Encoding: chunked`
  instead of Content-Length.
- `json.dumps` with the default `ensure_ascii=True` escapes non-ASCII as
  `ë` and would hide this bug, at the cost of larger payloads; the fix
  should not depend on that setting.
- A wrong length that is too *long* is just as bad: the client waits for
  bytes that never arrive until a timeout.

## Run
```bash
python -m unittest -v test_solution.py
```
