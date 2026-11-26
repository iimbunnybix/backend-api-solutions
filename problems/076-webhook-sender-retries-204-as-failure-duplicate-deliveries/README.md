# 076 · Customers' webhook handlers ran every event six times, and the dashboard said delivery had failed

## Problem
A SaaS platform sends signed webhooks (`invoice.paid`, `order.shipped`) to
customer endpoints and retries failed deliveries with backoff, up to
6 attempts. Several large customers moved their handlers to a queue-backed
design that answers `202 Accepted` right away, and others return
`204 No Content`.

From that day, those customers saw every event processed six times:
duplicate "payment received" emails, double fulfilment requests, and
inventory decremented six times per order. At the same time, the platform's
delivery dashboard marked their endpoints as failing and paged the
integrations team. A separate group of customers whose endpoints had been
deleted (`404`) or who rejected a payload (`400`) were being hit six times
per event for nothing.

## Root cause
The sender treated `status == 200` as the only success and retried
everything else. HTTP defines the whole **2xx** class as success, so `202`
and `204` deliveries were handled by the receiver *and* retried. The same
rule retried permanent client errors (most 4xx), which can never succeed on
a resend.

The model reproduces it: a receiver answering `204` processes the same event
**6** times, the sender still reports the delivery as failed after 6
attempts, and a `404` endpoint is called 6 times.

## Solution
`classify` decides by status class:

| Response | Meaning | Action |
|---|---|---|
| any 2xx | receiver accepted it | delivered, stop |
| 5xx, 408, 429, no response | temporary | retry with backoff |
| other 4xx | will fail again | give up, surface to the customer |
| 3xx | not followed | give up (a redirect could send a signed payload to an unregistered host) |

Every attempt carries the same event id, so a receiver that did get an
earlier attempt (for example, it processed the event but the response was
lost) can deduplicate. With the fix the `204` receiver processes the event
once, a `404` endpoint is called once, and a `503, timeout, 202` sequence is
delivered on attempt 3.

## Trade-offs
- **Duplicates are still possible.** A timeout after the receiver did the work
  is retried, so delivery is at-least-once. Receivers must deduplicate by
  event id; the sender can only make that possible, not unnecessary.
- **Giving up on 4xx can drop events** for a receiver with a transient bug
  that returns 400. Keep given-up events in a dead-letter list the customer
  can replay from the dashboard rather than discarding them.
- **429 should honour `Retry-After`** (problem 051) rather than the normal
  backoff. This model counts attempts and leaves timing out.
- Some providers disable an endpoint after repeated permanent failures. That
  is reasonable for 404/410, but needs a notification so the customer knows.

## Run
```bash
python -m unittest -v test_solution.py
```
