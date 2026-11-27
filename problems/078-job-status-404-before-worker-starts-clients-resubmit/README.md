# 078 · At month-end, one "Export invoices" click produced dozens of identical exports and dozens of export charges

## Problem
A billing platform sells invoice exports through its public API; each export
built is metered and billed to the customer's account. Customers use the
official SDK: `client.exports.create()` calls `POST /exports`, gets
`202 Accepted` with a job id, then polls `GET /exports/{id}` every 2 seconds
until the file is ready, for up to 60 seconds.

On normal days this worked. On the first business day of the month, when the
export queue backs up, customers found dozens of identical export files in
their account and matching export charges on their next bill. The SDK itself
timed out and told users the export had failed, so they clicked again and
made it worse. The export workers spent the morning building files nobody
would download, which pushed the backlog up further.

## Root cause
The API answered `202` after putting a message on the queue, but the job
record was only written by the worker when it took the message off the
queue. Until then, `GET /exports/{id}` found no record and returned `404`.
The SDK, reasonably, treats `404` for its own job as "the job was lost" and
submits a new one, whose status is also `404` until a worker reaches it.

In the model the queue backlog at peak is 9 seconds and the SDK polls every
2 seconds, so every first poll lands before the worker has started. One
`create()` call submitted **31** exports in its 60-second window, all 31 were
built and billed, and the SDK still ended on a `404`. With an off-peak
backlog of 1 second the same code submitted once, which is why it only
showed up at month-end.

## Solution
Make the job exist before anyone can ask about it:

1. `submit_export` writes a `PENDING` record **first**, then publishes the
   message, then answers `202` with the id and a `Location: /exports/{id}`
   header. By the time the client has the id, the record is there.
2. The worker claims a job by moving it `PENDING -> RUNNING`, and later
   `RUNNING -> DONE`. Out-of-order transitions raise, and a redelivered
   message for a job that is already `RUNNING` or `DONE` is ignored, so an
   at-least-once queue cannot build and bill the same export twice.
3. `GET /exports/{id}` returns 404 only for ids that were never accepted.
4. If publishing fails, the record is marked `FAILED` and the client gets
   `503` with `Retry-After`, never a `202` for a job that will not run.

At the same 9-second backlog, the SDK now submits once, sees
`pending -> running -> done`, and has its file after 14 seconds; one export is
built and billed.

## Trade-offs
- **Write-then-publish is two steps.** If the process dies between them, a
  `PENDING` job never runs. Use a transactional outbox (problem 006) or a
  sweeper that re-publishes `PENDING` jobs older than a few minutes.
- **Clients should also send an `Idempotency-Key`** (problem 001), so a
  genuinely lost response to `POST /exports` can be retried without a second
  job. The server fix stops the 404 loop; it does not cover that case.
- **The SDK treating 404 as "lost" is still risky.** A later SDK version should
  cap resubmissions, but old versions stay in use for years, so the server
  must not rely on that.
- The extra write per submission is cheap, but `PENDING` and `FAILED` records
  need the same retention and cleanup as finished ones.

## Run
```bash
python -m unittest -v test_solution.py
```
