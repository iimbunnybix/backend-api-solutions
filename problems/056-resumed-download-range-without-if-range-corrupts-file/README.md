# 056 · Customers who resumed an interrupted download got a "corrupt installer" error right after each new release

## Problem
A desktop app is distributed from `GET /downloads/app-installer`, a stable
URL that always serves the latest release. The download manager in the
website and the auto-updater both resume interrupted downloads instead of
starting again, which matters on slow or mobile connections where the
installer takes minutes.

On every release day, support got a wave of "installer is corrupt / checksum
mismatch" tickets, almost all from users on poor connections. Re-downloading
from scratch always worked, so the problem was blamed on the CDN for several
releases.

## Root cause
A resumed download is a second request: `Range: bytes=400-` asks for
everything after the 400 bytes already received, and `If-Range: "<etag>"`
says "only if the file is still the one with this ETag". The server ignored
`If-Range` and always returned `206 Partial Content` with bytes 400 onward of
the **current** file. If a new release was published between the first
request and the resume, the client glued the first 400 bytes of the old
installer to bytes 400-999 of the new one. In the test the spliced
1,000-byte file has the right length, but its SHA-256 matches neither
release, which is exactly the "checksum mismatch" users saw.

## Solution
- `handle_get` honours `If-Range` (RFC 9110 §13.1.5): if it does not match
  the current **strong** ETag, the range is ignored and the whole new file is
  sent with `200 OK`. A client that gets 200 instead of 206 discards its
  partial copy (`resume_download`).
- The ETag is derived from the file's bytes (SHA-256 prefix), so any change
  to the content produces a different tag.
- Weak ETags (`W/"..."`) never match, as the RFC requires for If-Range.
  An HTTP date in `If-Range` is also treated as a mismatch: one-second
  `Last-Modified` resolution cannot tell apart two releases published in the
  same second (see problem 048), and a full download is always safe.
- Range parsing is strict: one `bytes=START-` or `bytes=START-END` range,
  ASCII digits only and at most 19 of them; anything else is ignored and a
  full 200 is sent, which RFC 9110 allows. A range starting at or past the
  end gets `416` with `Content-Range: bytes */<size>`.

## Trade-offs
- After a mismatch the client re-downloads the whole file, which costs
  bandwidth on release days. Versioned URLs (`/downloads/app-2.3.1.exe`)
  avoid the problem entirely, because a URL's bytes never change; the stable
  URL can then redirect to the versioned one.
- Treating every date in `If-Range` as a mismatch disables resuming for
  clients that only send dates; adding date support requires a strong
  `Last-Modified` (the file unchanged for more than a second before it is
  served), which is easy to get wrong.
- Suffix ranges (`bytes=-500`) and multi-range requests are not supported
  and fall back to a full 200; real servers support them.
- Hashing the content on every request is fine for a small test file; a real
  server computes the ETag once at publish time and stores it.
- The client should still verify a published checksum after any download:
  If-Range protects against splicing, not against every kind of corruption.

## Run
```bash
python -m unittest -v test_solution.py
```
