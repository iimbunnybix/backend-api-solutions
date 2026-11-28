# 080 · Customers' partner integration kept disconnecting itself every hour with "invalid_grant"

## Problem
A CRM product syncs contacts with a partner's API on behalf of each
customer. A sync job runs 8 worker threads that share one OAuth client per
customer. Access tokens live for one hour; the partner rotates refresh
tokens, so every refresh returns a new refresh token and retires the old one.

Soon after the partner enabled refresh-token reuse detection, large
customers' integrations started showing "Disconnected: invalid_grant" about
an hour into a sync. Each customer had to sign in to the partner again, and
contacts edited in the meantime did not sync. Small customers, whose syncs
used one thread at a time, were never affected.

## Root cause
Every thread checked `now >= expires_at` on its own and, if true, called the
token endpoint with the refresh token it had just read. When the hour ran
out, all busy threads saw the expired token at the same moment and all sent
**the same** refresh token. The first request rotated it; the next one
presented a now-retired token. Under OAuth 2.0 security best practice
(RFC 9700, refresh-token rotation), a retired refresh token being used again
means two parties hold it, so the partner revoked the whole token family,
including the pair it had just issued to the first thread.

In the reproduction 8 threads make 8 refresh calls, 7 fail with
`InvalidGrant`, and even the one thread that "won" ends up holding an access
token the partner no longer accepts. A single thread refreshes fine, which
is why the client's unit tests passed.

## Solution
`TokenClient.get_access_token`:
1. Fast path: if the token is not near expiry, return it without locking.
2. Otherwise take a per-client lock and **check again**. A thread that waited
   on the lock finds the token another thread already refreshed and returns
   it; only a thread that still sees an old token calls the server.
3. Refresh `REFRESH_EARLY_SECONDS` (60 s) before expiry, so a request that
   starts just before the hour does not reach the partner with a token that
   expires on the way.

With the fix, 8 threads cause exactly 1 refresh call, all 8 receive the same
valid token, and three expiries in a row cause three refreshes.

## Trade-offs
- The lock is per process. Several processes or hosts sharing one
  customer's tokens need the refresh coordinated through shared storage (a
  row lock or a compare-and-swap on the stored token pair), or a single
  token service that all workers ask.
- Threads wait for the refresh call while holding no token. If the token
  endpoint hangs, every worker waits, so give the refresh call a timeout.
- Persist the new refresh token before using the access token. If the
  process crashes after the refresh but before saving, the next start
  presents the retired token and triggers revocation anyway.
- Refreshing early wastes a little of each token's lifetime; that is cheap
  compared with failed calls at the boundary.

## Run
```bash
python -m unittest -v test_solution.py
```
