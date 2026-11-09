# 044 · Invoice workers hung for 90 minutes on a call configured with "timeout=10", and month-end billing stalled

## Problem
A billing API builds invoice PDFs in a pool of 16 workers. For each invoice it
downloads the customer's tax breakdown from an internal tax service with
`requests.get(url, timeout=10)`. On the first day of the month the tax service
was overloaded: its responses did not fail, they *dripped*, a small chunk
every few seconds. Within minutes all 16 invoice workers were busy, the job
queue grew to tens of thousands of invoices, and dashboards showed workers
stuck in `socket.recv` for over an hour, although every outbound call had a
10-second timeout. Invoices went out a day late, and some customers' card
payments failed because the invoice reached them after the payment-run cut-off.

## Root cause
**The timeout was per read, not per request.** In `requests` (and in
`socket.settimeout`, `urllib3`'s read timeout, most HTTP clients' "read
timeout"), the read timeout is the longest the client will wait for *the
next bytes*. It is reset every time any data arrives. The `requests`
documentation says it directly: the timeout is not a time limit on the
entire response download.

So a server that sends a little data every 9 seconds never trips a 10-second
read timeout. In `solution.py`, `naive_download` reads a response sent as
600 chunks of 100 bytes, one every 9 s, with a 10 s read timeout. It
succeeds, returns all **60,000 bytes**, and takes **5,400 s (90 minutes)** of
fake-clock time. A single silence longer than 10 s would have raised, but a
slow, steady trickle never does. The same thing happens with slow uploads
and with proxies that send keep-alive bytes.

## Solution
`download_with_deadline` keeps the per-read timeout (it still catches a dead
connection quickly) and adds a **total deadline** for the whole response:

1. Compute `deadline = now + 30 s` once, before the first read.
2. Before every read, give it `min(read_timeout, time left)`, so the last
   read can never push the call past the deadline.
3. When the deadline is reached, raise `DeadlineExceeded` with how many bytes
   arrived, so logs show "slow" rather than "silent".

With the same trickling response the fixed client gives up after exactly
**30.0 s** (4 reads, 300 bytes received; the 4th read was only allowed the
3 s left), instead of 90 minutes. A healthy response (10 chunks, 0.2 s apart)
is returned in full, and a response finishing at 28.5 s still succeeds.

## Trade-offs
- The deadline must fit the largest legitimate response. For big downloads,
  a **minimum throughput** check (e.g. abort below 10 KB/s after the first
  5 s) adapts better than one fixed total.
- With `requests` there is no built-in total timeout; you stream the body
  and check a deadline between chunks (as here), or use a client that has
  one (`aiohttp`'s `ClientTimeout(total=...)`, or `asyncio.timeout()` around
  the whole call). `httpx`'s read timeout is also per read.
  The deadline should also cover connecting and DNS, not only the body.
- If the call is part of a larger request, the deadline should come from the
  caller's remaining budget (deadline propagation, problem 008), not a fixed
  30 s.
- Giving up does not stop the server's work; the tax service still spends
  effort on responses nobody reads. Pair this with load shedding upstream.

## Run
```bash
python -m unittest -v test_solution.py
```
