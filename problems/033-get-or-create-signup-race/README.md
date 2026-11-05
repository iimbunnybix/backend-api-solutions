# 033 · New users tapping "Continue with Google" got "Something went wrong" on their very first login

## Problem
A consumer app's login endpoint handles "Continue with Google": it takes the
verified email from the identity token, looks the user up, and creates an
account if none exists. Returning users were fine. New users regularly got
"Something went wrong" on their first attempt, then logged in normally on the
retry. It showed up as a steady stream of HTTP 500s tagged
`UNIQUE constraint failed: users.email`, concentrated on launch days and ad
campaigns, exactly when the most new users arrive and first impressions
matter most.

Months earlier, before the email column had a unique index, the same code
had quietly created **two accounts** for some people. Support had to merge
them by hand after users reported "my order history disappeared" when the
app picked the other account.

## Root cause
A **check-then-insert race**. The code did:

```python
user = find_user(email)          # SELECT ... WHERE email = ?
if user is None:
    insert_user(email, name)     # INSERT INTO users ...
```

The mobile app sent the login call twice on a double tap (and a background
prefetch sometimes sent a third). Both requests ran the `SELECT` before either
ran the `INSERT`, so both saw "no user" and both inserted. Checking first does
not prevent this, because nothing stops another request from inserting in the
gap between the check and the write.

- **No unique index:** both inserts succeed, so one person gets two accounts.
- **With the unique index** (added after the duplicates were found): the
  database correctly rejects the second insert, but the code never expected
  that, so the error became a 500.

The tests reproduce this by running request B inside request A's
check-insert gap. Without the index the double tap leaves 2 rows; with it the
statuses are `[201, 500]`. In a burst of 100 double-tapped first logins, all
100 produce one 500.

## Solution
`get_or_create` in `solution.py` lets the **database's unique constraint pick
the winner** instead of the application:

1. **Read first** for the common case (a returning user): one `SELECT`, no write.
2. If the user is missing, `INSERT ... ON CONFLICT (email) DO NOTHING`. If
   another request inserted first, this is a no-op instead of an error.
   `rowcount` says whether this request created the row.
3. **Re-read** the row and return it, so the request that lost the race returns
   the winner's account (same id, same name) with `created = False`.

The same double tap now returns `[201, 200]` and leaves one row; the burst of
100 double-tapped signups gives 100 × 201, 100 × 200, 0 errors and 100 rows.
The fix depends on the unique index: without it SQLite rejects
`ON CONFLICT (email)` outright ("does not match any PRIMARY KEY or UNIQUE
constraint"), which is a useful guard against deploying it on the old schema.

## Trade-offs
- **The unique constraint is the real fix.** Application-level locks or
  "check harder" logic do not work across processes; the index does. Existing
  duplicates must be merged before the index can be created.
- **Isolation level matters.** In PostgreSQL under the default READ COMMITTED,
  `INSERT ... ON CONFLICT DO NOTHING` waits for the other transaction and the
  re-read sees its committed row. Under REPEATABLE READ or SERIALIZABLE the
  re-read may not see it (or the transaction may fail with a serialization
  error), so run this step in its own short transaction or retry.
- `ON CONFLICT ... DO UPDATE ... RETURNING` returns the row in one statement
  but turns every login into a write (and bumps updated_at / fires triggers);
  `DO NOTHING` plus a re-read keeps returning users read-only.
- The alternative of catching the unique-violation error and re-reading also
  works, but inside a PostgreSQL transaction the error aborts the transaction
  unless a savepoint is used. `ON CONFLICT` avoids the error entirely.
- Debouncing the button in the app reduces double requests but cannot remove
  them (retries, prefetch, two devices), so the server must still be safe.

## Run
```bash
python -m unittest -v test_solution.py
```
