# 032 · Big merchants' "Export orders" button always ended in a 504, and every retry made the database slower

## Problem
A commerce platform's merchant dashboard has an "Export orders (CSV)" button
that calls `GET /reports/orders.csv`. Small merchants got their file in a
couple of seconds. Large merchants with 500,000 orders a month always saw
`504 Gateway Timeout` after a minute. They clicked again, and again. Each click
started another full export on the reporting database, so at month-end the
replica was busy with exports nobody would ever receive, and normal dashboard
pages slowed down for every merchant.

## Root cause
The export was built **inside the HTTP request**. At about 0.2 ms per row, a
500,000-row export takes 100 seconds, but the load balancer closes requests
that send nothing for 60 seconds (the default idle timeout on AWS Application
Load Balancers). The load balancer answered 504, yet it cannot cancel the
backend, which kept working on a response that went nowhere.

In the reproduction, three clicks give three 504s and 300 seconds of backend
work. A 10,000-row export (2 seconds) still worked, which is why the problem
only showed up for the largest customers.

## Solution
Make the export an asynchronous job (`ExportService` in `solution.py`):

1. `POST /exports` stores a job and immediately returns `202 Accepted` with a
   `status_url` and a `Retry-After` hint (5 s), so clients poll politely.
2. A background worker builds the file outside any HTTP request, oldest job
   first, so no request has to stay open longer than the load balancer allows.
3. `GET /exports/{id}` returns `pending` / `running` / `done` with a download
   link, `410 Gone` once the file has passed its 24-hour TTL, and `404` for
   unknown jobs **and** for other users' jobs, so job IDs cannot be probed.
4. A repeated click with the same user and filters (in any key order) while a
   job is pending or running returns the existing job. Three clicks now cost
   100 seconds of work, not 300.

## Trade-offs
- More moving parts: a job table, a worker, file storage and cleanup. For
  exports that are always small, synchronous is simpler.
- Polling adds requests; a webhook or an email with the link avoids it, and
  `Retry-After` keeps polling cheap.
- Raising the load balancer timeout is a quick stop-gap, but it keeps workers
  tied up, still fails for the next larger customer, and does not stop retries
  from duplicating work.
- Streaming the CSV as it is generated keeps the connection alive, but a
  dropped connection still loses all progress and cannot be resumed.
- Jobs are deduplicated only while active, so a user can ask for fresh data
  after a job finishes. A real system should also cap concurrent jobs per user.
- The in-memory job store here is for clarity; in production it lives in a
  database or queue so jobs survive a restart.

## Run
```bash
python -m unittest -v test_solution.py
```
