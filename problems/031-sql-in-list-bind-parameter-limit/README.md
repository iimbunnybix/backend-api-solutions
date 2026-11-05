# 031 · Support console returned HTTP 500 whenever an agent selected more than a thousand customers

## Problem
A support console lets agents tick customers in a search result and open
"load selected" to send a bulk notice or apply a goodwill credit. During a
delivery outage agents selected every affected customer in a region, a few
thousand rows, and the endpoint failed with HTTP 500 every time. Small
selections worked, so the bug had shipped unnoticed. The workaround (selecting
in batches of a few hundred by hand) cost hours on the day customers most
needed a reply.

## Root cause
The endpoint built one statement with one bind parameter per ID:

```sql
SELECT id, name FROM customers WHERE tenant_id = ? AND id IN (?, ?, ?, ...)
```

Databases cap the number of bind parameters per statement. The service's
SQLite build used the historical default of 999 (SQLite raised the default to
32,766 in 3.32.0; PostgreSQL's wire protocol caps a statement at 65,535).
With the extra `tenant_id` parameter, 999 IDs already make 1,000 parameters, so
the database raised `too many SQL variables` and the error bubbled up as a
500. The tests set the same 999 limit on an in-memory database to reproduce it.

## Solution
`load_customers` in `solution.py`:

1. Rejects requests above an API cap (10,000 IDs) with a client error, so one
   request cannot trigger an unbounded number of queries.
2. Removes duplicate IDs while keeping the first-seen order.
3. Reads the live limit from the connection (`getlimit`), subtracts the
   parameters the statement needs for other filters, and splits the IDs into
   chunks of that size. 5,000 IDs become 6 queries of at most 998 IDs.
4. Merges the results and returns them in the order the caller asked for,
   because each chunk comes back in index order.

The tenant filter is applied in every chunk, and an empty selection runs no
query at all.

## Trade-offs
- Chunks are separate statements, so they are not one consistent snapshot
  unless you wrap them in a transaction. For a read-only lookup that is usually
  fine.
- Many IDs mean many round-trips. For very large sets, loading the IDs into a
  temporary table and joining, or (PostgreSQL) passing one array parameter with
  `id = ANY(?)`, uses one statement regardless of size.
- A very long `IN` list also makes the query planner work harder; chunking keeps
  each statement small and its plan predictable.
- The API cap is a product decision: beyond it, a background job (see the async
  export problem) is a better shape than a synchronous request.

## Run
```bash
python -m unittest -v test_solution.py
```
