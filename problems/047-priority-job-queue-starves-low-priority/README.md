# 047 · Invoice emails stopped going out for a whole afternoon while order webhooks were processed instantly

## Problem
An e-commerce backend runs one background worker pool fed by a priority job
queue. Order webhooks and payment callbacks are `HIGH`, search re-indexing is
`NORMAL`, and invoice emails and report generation are `LOW`.

During a marketplace sale, order webhooks arrived as fast as the workers could
process them for several hours. Webhook latency looked perfect, but no invoice
email was sent the whole afternoon. Customers who needed an invoice for
expense claims opened support tickets, and finance could not close the day
because its daily report job never started.

In the simulation in `solution.py` (one worker, one `HIGH` job per second, which
is exactly the worker's capacity, and five `LOW` invoice jobs queued at t=0),
the original queue runs **0 of the 5 invoice jobs in 10 minutes**, while every
webhook starts with **0 s** wait.

## Root cause
The worker used **strict priority**: always take the highest-priority job,
oldest first within a priority. That only works if high-priority work
regularly runs out. Under sustained load the `HIGH` queue was never empty, so
a `LOW` job's wait had no upper bound: it was not slow, it was never chosen.
Nothing in the queue looked at how long a job had already waited.

## Solution
`aging_pick` adds **aging**: a job is promoted one priority level for every
`AGING_SECONDS` (30 s) it has waited.

1. `effective_priority` = base priority minus full aging intervals waited,
   never better than `HIGH`.
2. The worker picks the best effective priority, oldest first on a tie, so a
   promoted invoice queues *behind* older webhooks, not in front of them.
3. A `LOW` job competes as `HIGH` after 2 × 30 s = 60 s
   (`max_wait_bound`), and from then on it is served in arrival order.

With the same load, all **5 invoice jobs start after 60–64 s**, and the worst
webhook wait rises from 0 s to **5 s** (one second per promoted job). A fresh
`HIGH` job still beats a fresh `LOW` job.

## Trade-offs
- Aging moves latency from high to low priority jobs: during the sale each
  promoted job delays webhooks by its run time. Long low-priority jobs (a
  10-minute report) should run on a separate pool instead (see problem 015).
- The bound holds only while the worker keeps up with `HIGH` arrivals. If
  arrivals exceed capacity, every queue grows without limit and the fix is
  more workers or load shedding, not scheduling.
- Weighted fair queuing (for example 8 `HIGH` : 2 `NORMAL` : 1 `LOW` turns) is
  an alternative that gives each level a guaranteed share instead of a
  maximum wait; it is simpler to reason about for throughput, aging is simpler
  for "every job within N seconds" promises.
- Effective priority changes with time, so a real heap cannot store it as a
  fixed key; keep one FIFO queue per priority and compare only the heads
  (the oldest job in each level ages fastest), which is O(levels) per pick.

## Run
```bash
python -m unittest -v test_solution.py
```
