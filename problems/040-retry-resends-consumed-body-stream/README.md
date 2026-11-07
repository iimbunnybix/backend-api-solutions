# 040 · Uploaded contracts showed up as empty 0-byte files after a network blip, with a 201 to the client

## Problem
A document-management API stores signed contracts. A backend job streams each
PDF from object storage to the API with a POST, and wraps the call in a
three-attempt retry loop for `ConnectionResetError`, because the corporate
proxy in front of the API resets idle or draining connections. After a proxy
upgrade, resets became common during deploys. Every retry "succeeded" with a
`201 Created`, but customers opening those contracts found empty 0-byte files.
The originals had already been deleted from the staging bucket after the 201,
so each one had to be re-collected from the signer.

## Root cause
**The request body was a one-shot stream, and the retry reused it.** The body
was a generator that read the PDF from storage chunk by chunk (in other
places, an open file object). The first attempt read the stream to the end;
the proxy then reset the connection before the response arrived. The retry
handed the *same* exhausted stream to the transport, which sent zero bytes,
and the API accepted an empty upload. A retry is only safe if it sends the
same request again, and "the same request" includes the whole body.

## Solution
`solution.py` keeps `naive_post_with_retry` and adds `post_with_retry`,
which first turns any body into a `ReplayableBody` with `make_replayable`:

- **bytes** are simply reused for each attempt;
- **seekable files** remember their position when the call starts and are
  rewound to that offset (not to 0) before every attempt, so a caller that
  wrote a header first still sends only the payload;
- **one-shot iterators** (generators, pipes) are buffered in memory up to
  `MAX_BUFFER_BYTES` (8 MiB) and then replayed from the buffer;
- a stream **larger than the limit** is sent once (the buffered head followed
  by the rest of the stream) and is **not retried**: the error reaches the
  caller instead of a silent empty upload. Its `chunks()` refuses to be
  called twice, so a future code change cannot reintroduce the bug.

The tests use a fake server that reads the whole body and then resets the
connection, the same shape as the incident: the naive client stores `b""`,
the fixed client stores all 26,000 bytes on every attempt.

## Trade-offs
- Buffering costs memory: up to 8 MiB per in-flight upload. Tune the limit to
  your concurrency, or spool to a temporary file when a stream is too large
  to hold but must still be retryable.
- Large streams that are not retried still fail on a reset. For big files use
  a resumable or multipart upload protocol (upload parts, retry each part,
  then commit), which also avoids resending gigabytes.
- The server should defend itself too: require a `Content-Length` or a
  checksum header (for example `Content-MD5` / `x-amz-checksum-sha256`) and
  reject bodies that do not match, and reject empty uploads where they are
  never valid.
- Retrying a POST at all is only safe if the endpoint is idempotent; combine
  this with an idempotency key (problem 001) so the first, actually stored,
  attempt is not duplicated.
- Mature clients handle part of this (for example `requests` rewinds file
  bodies on redirects), but generators are generally not replayable, so
  check what your client and retry library do with streaming bodies.

## Run
```bash
python -m unittest -v test_solution.py
```
