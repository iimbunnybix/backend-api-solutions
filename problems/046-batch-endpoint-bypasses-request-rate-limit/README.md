# 046 · A customer's sync script ran 120 times over its API quota and was never rate limited

## Problem
A CRM's public contacts API allows each API key **100 requests per minute**,
enforced with a token bucket. It offers `GET /contacts/{id}` and a batch
endpoint, `POST /contacts:batchGet`, that accepts up to 100 ids per call.

One customer moved their nightly sync from single lookups to the batch
endpoint and ran it with two workers. Database read load jumped, p99 latency
of the contacts API rose for every tenant on that shard, and the rate-limit
dashboard showed the customer comfortably *under* their quota the whole time.

In the simulation in `solution.py` (100 ids per call, 2 calls per second),
the original limiter allows every call: **12,000 contact lookups in one
minute** against a quota of 100, and 109,800 in ten minutes.

## Root cause
The limiter counted **requests**, but the cost to the system is the number of
**contacts looked up**. Every call took one token whatever its size, so a
100-id batch cost the same as a single lookup. Batch endpoints exist to make
calls cheaper for the network, not to make the work free; charging per call
turned them into a 100x quota multiplier.

## Solution
`batch_get` charges by work done:

1. Cost = number of ids, with a minimum of one token, so an empty call is not
   free either.
2. The single and batch endpoints draw from the **same bucket**, so a client
   cannot get two quotas by mixing them.
3. A batch whose cost is larger than the bucket's capacity can never succeed,
   so it is rejected with **400** and a message to split it, instead of a 429
   the client would retry forever. Oversized batches (over 100 ids) are still
   rejected before any token is taken.
4. A denied call is not charged and gets a `Retry-After` rounded **up** to a
   whole second, so retrying at that time succeeds (after a full batch, a
   50-id call is told to wait 30 s).

With the same sync script, lookups drop to **100 in the first minute** and
**1,000 in ten minutes**, exactly the 100-per-minute quota.

## Trade-offs
- Ids are an approximation of cost; a lookup that expands related records or
  a search query may cost far more. Some APIs charge by measured cost after
  the fact (GitHub's GraphQL "points", Shopify's query cost), which is more
  accurate but harder for clients to predict.
- Customers who were relying on the loophole will start getting 429s;
  announce the change, show per-key usage in the same unit, and consider a
  higher id-based quota so legitimate syncs keep working.
- Rejecting batches larger than the bucket couples the max batch size to the
  plan's quota; tiny plans need a smaller maximum or a separate bulk export.
- The cost must be validated (positive, bounded) before charging; a negative
  cost would refill the bucket (see problem 002).

## Run
```bash
python -m unittest -v test_solution.py
```
