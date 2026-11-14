# 054 · A hospital night shift ended up with zero doctors on call after two leave requests at the same second

## Problem
A hospital rota service let doctors mark themselves off call from a mobile
app. The rule was simple: every shift must keep at least one doctor on call,
and the API checked it before saving. One night the two doctors on a ward's
night shift both tapped "go off call" within the same second. Both requests
returned 200, the shift showed nobody on call, and the paging system had no
one to page for a ward of inpatients until a nurse noticed hours later. The
check passed in every test and in code review.

## Root cause
Each request ran in a transaction at snapshot isolation (PostgreSQL's
`REPEATABLE READ` is snapshot isolation, and so is Oracle's `SERIALIZABLE`):

1. Alice's transaction reads the shift: 2 doctors on call, so 1 would remain. OK.
2. Bob's transaction reads its own snapshot: also 2 on call. OK.
3. Alice writes *her* row to `off_call` and commits.
4. Bob writes *his* row to `off_call` and commits.

Snapshot isolation only rejects a commit when another transaction wrote the
**same row** first. The two transactions wrote different rows, so neither
conflicted, yet together they broke a rule that depended on both rows. This
anomaly is called *write skew*. `test_concurrent_requests_leave_nobody_on_call`
reproduces it with a small multi-version store (`SnapshotStore`) that uses the
same first-committer-wins rule.

## Solution
`go_off_call` first increments a guard row for the shift and then does the
same check. Because every leave request for that shift now writes the same
row, two concurrent requests are a write-write conflict: the first commit
wins and the second gets `SerializationConflict`. `run_with_retry` restarts it
with a fresh snapshot, so it decides again on current data: with two doctors
it gets `NotEnoughCover`; with three it succeeds and one doctor stays on call.
Requests for different shifts touch different guard rows and never conflict.

## Trade-offs
- In a real database the same idea is `SELECT ... FROM shifts WHERE id = ?
  FOR UPDATE` (lock the parent row before checking the children), which
  blocks instead of failing; or run the transaction at `SERIALIZABLE`, where
  PostgreSQL's SSI detects the read/write dependency and aborts one side.
  Either way the caller must retry on serialization failures.
- The guard row serialises all leave requests for one shift. That is fine
  for a rota, but would be a hot row for high-traffic invariants such as
  "seats left in a stadium" (see problem 007 for single-row optimistic
  locking, and sharded counters in system design).
- Every code path that changes on-call status must take the guard; one
  forgotten path (an admin tool, a batch import) brings the bug back. A
  database constraint or trigger is harder to bypass but harder to express
  for multi-row rules.
- Retries are bounded (`MAX_ATTEMPTS = 3`) so a hot shift reports an error
  instead of looping forever.

## Run
```bash
python -m unittest -v test_solution.py
```
