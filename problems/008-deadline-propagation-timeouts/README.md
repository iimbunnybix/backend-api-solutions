# 008 · Customers saw "checkout timed out", retried, and found their card charged twice

## Problem
The API gateway gives every checkout request a 2-second budget and returns
504 to the app when it runs out. Behind it, the checkout service calls
inventory, then pricing, then payment. When the inventory database slowed down
during an evening peak, the support queue filled with the same story: the app
said checkout failed, the customer tapped *Pay* again, and the bank statement
showed two charges. Finance refunded the duplicates by hand, and the slow
period also showed checkout pods busy with requests whose clients were gone.

## Root cause
**Timeouts were per hop, not per request.** Each downstream call had its own
fixed 2 s timeout, so the handler could legitimately run for up to 3 × 2 s,
far beyond the gateway's 2 s. In the reproduction, inventory and pricing take
1.5 s each and payment 0.4 s: the gateway gives up at 2.0 s, but the handler
keeps going and charges the card between 3.0 s and 3.4 s. Nobody receives that
success, the customer retries, and the second attempt charges again. With
all three dependencies at 1.9 s, one request runs for 5.7 s.

Work done after the caller has gone also makes overload worse: each abandoned
request still holds a worker and still hits the slow dependency.

## Solution
**One deadline per request, propagated to every hop.**

1. The edge creates a `Deadline` (now + 2 s). Every outgoing call uses
   `timeout = min(per_call_cap, deadline.remaining())`. In the same scenario,
   pricing gets only the 0.5 s that is left, times out, and payment is never
   called. The request ends at exactly 2.0 s.
2. `call_with_deadline` refuses to start a call when less than `min_useful`
   is left. For the payment (a side effect) the reserve is larger (0.3 s): it
   is better to fail *before* moving money than to race the client's timeout.
3. The remaining budget travels downstream as `X-Request-Timeout-Ms`
   (remaining milliseconds, not an absolute timestamp, so clock skew between
   hosts does not matter). The receiver clamps it to a server maximum, uses a
   default when it is missing or malformed, and treats ≤ 0 as already expired.
4. A property-style test checks several latency mixes: total time never
   exceeds the 2 s budget.

## Trade-offs
- The header budget ignores network transit time, so downstream deadlines are
  slightly optimistic. Subtract a small margin per hop if that matters.
- A deadline makes duplicate charges much rarer but cannot remove them: a
  charge can still succeed at the provider while its response is lost. The
  payment call still needs an **idempotency key** (see problem 001).
- Too tight a budget turns latency into errors; set it from the real p99 of
  the whole call chain, not from guesses per hop.
- gRPC propagates deadlines natively; with plain HTTP every service and client
  library must agree on the header and honour it, or the chain breaks silently.

## Run
```bash
python -m unittest -v test_solution.py
```
