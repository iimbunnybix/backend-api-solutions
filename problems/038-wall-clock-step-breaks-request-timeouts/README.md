# 038 · After an NTP correction, stuck requests never timed out, then every request was cancelled at once

## Problem
A document-conversion API runs a watchdog thread that cancels any request
running longer than 30 seconds and frees its worker. One morning a host's
clock had drifted about an hour ahead (a VM restored from a snapshot); NTP
stepped it back. For the next hour the watchdog cancelled nothing: requests
stuck on a hung storage mount held all 64 workers and the instance stopped
serving, while dashboards showed negative request latencies. On another host
the opposite happened: its clock was stepped *forward*, and the watchdog
cancelled every in-flight request in the same second, returning a burst of
504s for uploads that had only just started.

## Root cause
**Durations were measured with the wall clock.** The watchdog computed
`time.time() - started_at`. `time.time()` answers "what time is it?" and may
jump in either direction when the clock is corrected. A difference of two
wall-clock readings is only a duration if nobody touched the clock in
between.

In the reproduction, after a one-hour step back and 10 minutes of real time,
the stuck request's elapsed time is negative and the watchdog reports nothing.
After a one-hour step forward, 5 requests that are 0.2 s old are all reported
as timed out, and a 50 ms request is recorded as taking -1.95 s.

This is different from devops problem 016 (detecting clock *skew between
hosts*): here a single host's clock is fine afterwards, but any duration that
spans the correction is wrong.

## Solution
`TimeoutWatchdog` takes its clock as a parameter. `naive_watchdog` wires it
to the wall clock (the original); `safe_watchdog` wires it to the monotonic
clock (`time.monotonic()` in production). A monotonic clock cannot go
backwards and is not affected by wall-clock steps, so elapsed times stay
correct: the stuck request times out after exactly 30 s of real time, fresh
requests survive a forward step, and latencies are the real 0.05 s.

The tests use `FakeHostClocks`, which keeps a wall clock and a monotonic clock
side by side and can step the wall clock alone, so the NTP correction is
reproduced deterministically.

## Trade-offs
- **Monotonic readings mean nothing across processes or reboots.** They are
  only for durations inside one process. Deadlines sent to other services
  (see problem 008) still need a relative budget or wall time plus a skew
  allowance.
- **Keep wall time for humans.** Log the wall-clock start time for
  correlation, but compute timeouts, retries, rate-limit windows and latency
  metrics from the monotonic clock.
- **`time.monotonic()` may or may not count time while the machine is
  suspended**, depending on the OS; for a laptop or a paused VM, "elapsed" can
  differ from calendar time. For server timeouts that is usually what you
  want.
- Configuring NTP to *slew* (adjust gradually) instead of step reduces the
  jumps but does not remove them (large offsets, snapshots, manual changes),
  so the code should not rely on it.

## Run
```bash
python -m unittest -v test_solution.py
```
