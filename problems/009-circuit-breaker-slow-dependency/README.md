# 009 · Product pages went down because the optional "you may also like" widget's service was slow

## Problem
The product page handler runs on a pool of 40 worker threads and receives
about 200 requests per second. Each page calls a recommendations service for
the "you may also like" strip, with a 3-second client timeout. The page is
designed to render fine without recommendations.

During a recommendations deploy that left its pods hanging, product pages
started returning 503s and then timing out at the load balancer. Product pages
are on the path to checkout, so conversion fell for the whole outage, even
though only a cosmetic widget was actually broken.

## Root cause
**Waiting is not free.** A failing dependency that errors fast is cheap; a
*hanging* one makes every caller wait for the full timeout while holding a
worker thread. By Little's law a pool serves at most `workers / service_time`
requests per second:

| | Time per request | Pool capacity |
|---|---|---|
| Healthy (50 ms recs + 20 ms own work) | 0.07 s | 571 req/s |
| Recs hanging (3 s timeout + 20 ms) | 3.02 s | 13 req/s |

With 200 req/s arriving and a capacity of 13 req/s, the request queue grows
without bound, so the whole page goes down. The code caught the
`TimeoutError` and rendered the fallback correctly, but only *after* waiting
3 seconds for it, every single time. In the reproduction, 1,000 page views
during the outage spend 3,000 worker-seconds waiting on recommendations.

## Solution
A **circuit breaker** around the recommendations call (`CircuitBreaker` in
`solution.py`):

- **Closed:** calls pass through. The breaker keeps the outcomes of the last
  20 calls; errors *and* calls slower than 1 s count as failures, because a
  slow success hurts just as much as an error.
- **Open:** once at least 10 calls are in the window and ≥ 50% failed, the
  breaker opens and returns the fallback (an empty strip) immediately, without
  touching the network, for 30 s.
- **Half-open:** after the cool-down, at most 3 concurrent probe calls go
  through. If all succeed the breaker closes; one failure opens it again.

In the same 1,000-request outage, only the first 10 calls reach the hanging
service (30 worker-seconds instead of 3,000) and the other 990 are
short-circuited. The tests also cover recovery, a failed probe re-opening the
breaker, probe concurrency, and that a 20% background error rate does not trip it.

## Trade-offs
- During the cool-down, the widget stays empty even if the dependency has
  already recovered. A shorter `open_seconds` recovers faster but probes a
  sick service more often.
- Thresholds need tuning per dependency: low-traffic endpoints may never reach
  `min_calls`, and a time-based window can suit them better than a count-based one.
- A breaker per process means each instance has to learn about the outage on
  its own (10 slow calls each). That is usually acceptable; sharing breaker
  state across instances adds coupling and a new failure mode.
- It only helps when there is a sensible fallback. For required dependencies
  (payment), fail fast with a clear error instead of degrading.
- Complementary controls: a **tighter timeout** (3 s is too long for a cosmetic
  widget) and a **bulkhead** (a separate, small pool or semaphore for recs calls)
  so even the first slow calls cannot use up the main pool.

## Run
```bash
python -m unittest -v test_solution.py
```
