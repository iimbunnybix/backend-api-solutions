# 058 · A user tapped "Delete selected" with nothing selected and every customer's notes disappeared

## Problem
A note-taking app's API has a bulk delete endpoint,
`DELETE /notes?ids=4,7,9`, used by the web and mobile clients when a user
selects several notes and taps "Delete selected". A new version of the web
client sent the request even when the selection was empty, producing
`DELETE /notes?ids=`.

The first time a user did that, the `notes` table went from millions of rows
to zero in one statement. Every customer lost their notes, the app showed
empty lists everywhere, and the team had to restore from the nightly backup,
losing the day's edits for everyone and spending most of the day on the
restore and support tickets.

## Root cause
The handler built its SQL by adding the filter only when there was
something to filter on:

```python
sql = "DELETE FROM notes"
if ids:
    sql += f" WHERE id IN ({placeholders})"
```

`"".split(",")` is `[""]`, the empty string was filtered out, `ids` was
empty, and the `WHERE` clause was simply never added. The resulting
`DELETE FROM notes` is valid SQL that means "delete everything". In the
tests, `naive_delete_notes(db, "")` and `naive_delete_notes(db, ",")` delete
all 5 rows of the fixture, including the other user's notes.

Two design choices made this possible: an empty filter was treated as "no
filter" (match all) instead of "match nothing", and the delete was not scoped
to the caller, so even the normal path could delete another user's notes by
id.

## Solution
- **An empty or malformed filter is a 400.** `parse_ids_param` rejects `""`,
  `","`, `"1,,2"`, non-numeric and negative ids, and more than
  `MAX_IDS_PER_REQUEST` (500) ids, before any SQL is built.
- **The WHERE clause is not optional.** `build_delete_by_ids` has no code
  path that returns SQL without `WHERE owner_id = ? AND id IN (...)`, and it
  refuses an empty list itself, so a future caller that skips the parser
  still cannot widen the delete.
- **Every delete is scoped to the caller** (`owner_id = ?`), so ids belonging
  to another user delete nothing.
- **Defence in depth:** inside the transaction, if the affected row count is
  larger than the number of requested ids, the handler raises and the
  transaction rolls back.
- **"Delete all" is a separate call** (`delete_all_notes`) that needs an
  explicit confirmation string and still only deletes the caller's notes.

## Trade-offs
- Clients that relied on `?ids=` being a harmless no-op now get a 400; that is
  the point, but they must stop sending the request when nothing is selected.
- The row-count check only catches over-deletes after they are executed (and
  then rolls them back); it does not replace the WHERE clause. It also costs
  nothing because the count comes back with the statement.
- Soft delete (a `deleted_at` column with a retention period) would have made
  the incident recoverable without a restore, at the cost of filtering
  deleted rows everywhere (see problem 035 for its unique-index pitfall).
- Database-side guards add another layer: MySQL's `sql_safe_updates` refuses
  `DELETE` without a key in the `WHERE` clause, and row-level security in
  PostgreSQL enforces the owner scope even if application code forgets it.
- The 500-id cap means very large selections must be split into several
  requests or handled by an asynchronous job.

## Run
```bash
python -m unittest -v test_solution.py
```
