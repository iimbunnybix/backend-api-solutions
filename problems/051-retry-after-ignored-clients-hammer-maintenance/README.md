# 051 · Every order page failed during a 2-minute maintenance window, even though the API said "come back in 120 seconds"

## Problem
The inventory API went into planned maintenance for two minutes. It answered
every request with `503 Service Unavailable` and `Retry-After: 120`. The
order-page backend called it through a shared HTTP client that retried up to
5 times with exponential backoff (0.1 s, 0.2 s, 0.4 s, 0.8 s). In the test,
that client sends all 5 requests within **1.5 seconds** and then fails, so
every order page during the window showed an error and each caller sent five
times the traffic to a service that had already said it was not ready.

## Root cause
The client treated every 503 the same and used only its own retry schedule.
The server's `Retry-After` header — the one piece of information that said
*when* a retry could succeed — was never read.

## Solution
`polite_get`:
1. Parses `Retry-After` in both forms allowed by RFC 9110: a number of
   seconds (`120`) or an HTTP-date (`Fri, 15 Jan 2027 08:02:00 GMT`).
   A date in the past means "retry now"; unparseable or negative values are
   ignored and the client falls back to exponential backoff.
2. Caps the hint at 300 s so a misconfigured server cannot park workers.
3. Checks the caller's deadline before sleeping. If the server's wait does not
   fit, it fails immediately instead of retrying early (which cannot succeed).

With the fix the client makes **2 requests** (one sleep of 120 s) and succeeds;
with a 10 s deadline it returns the 503 after **1 request** with no sleeping.

## Trade-offs
- Honouring long Retry-After values is only right for background or async
  work. User-facing requests usually have short deadlines, so they fail fast;
  showing a "try again in 2 minutes" message is better than a hung page.
- Many clients waiting exactly 120 s return at the same instant. Adding a
  little jitter on top of the hint spreads that reconnect spike.
- Some servers send Retry-After on other statuses (e.g. 301, 413); this client
  only retries 429 and 503.

## Run
```bash
python -m unittest -v test_solution.py
```
