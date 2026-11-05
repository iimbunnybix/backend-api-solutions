# 035 · Users who deleted their account got "email already registered" when they tried to come back

## Problem
A subscription app lets users delete their account from the settings page.
Deletion is a soft delete: the row gets a `deleted_at` timestamp and is kept
for a 30-day retention period (chargebacks, legal holds), then purged by a
nightly job. Support started getting tickets from former customers who wanted
to resubscribe: sign-up said *"This email is already registered"*, and the
password-reset flow said *"No account found"*, because it correctly ignored
deleted rows. The only way back in was a support agent editing the database.
At the same time, team admins complained that people they had removed still
appeared in the member list and in the seat count they were billed for.

Under pressure, a hot fix dropped the unique constraint on `email` and moved
the check into application code. Re-signup worked, but within days the data
team found pairs of *live* accounts with the same email: users who
double-clicked "Create account" or had two tabs open. Logins then picked one
of the two at random, and the other account's orders seemed to disappear.

## Root cause
**The uniqueness rule and the data model disagreed.** The business rule is
"at most one *live* account per email", but the schema said "at most one
*row* per email" (`UNIQUE (email)`), and a soft-deleted row is still a row. So
the deleted account kept owning its email until the purge.

Dropping the constraint replaced a too-strict rule with none. A
check-then-insert in code is a race: two requests both run
`SELECT ... WHERE email = ? AND deleted_at IS NULL`, both see nothing, and
both insert. Only the database can make that check atomic.

Listings were the same mistake in another place: every query read the raw
`accounts` table, so each endpoint had to remember `deleted_at IS NULL`, and
the member list did not.

In the reproduction: with the original schema, signing up again after a soft
delete raises `EmailTaken`, and the listing still returns the deleted account.
With the constraint dropped, two sign-ups that both pass the check leave
**2 live accounts** for one email.

## Solution
`solution.py`:

1. **Partial unique index.** `CREATE UNIQUE INDEX accounts_live_email ON
   accounts (email) WHERE deleted_at IS NULL`. Only live rows are in the
   index, so deleted rows never block a new sign-up, and the database still
   rejects a second live account atomically. PostgreSQL and SQLite support
   this syntax directly.
2. **The pre-check is only for a friendly message.** `sign_up` still checks
   first, but `insert_account` turns the index's `IntegrityError` into
   `EmailTaken`, which is what actually stops the double-click race.
3. **Restore respects the rule too.** Restoring an old account whose email
   has since been reused fails with `EmailTaken` instead of silently creating
   a duplicate, because restore is just another write into the index.
4. **Listings exclude deleted rows by default.** `list_accounts` adds
   `WHERE deleted_at IS NULL` unless the caller passes `include_deleted=True`
   (admin and audit tools). Making "live only" the default means a new
   endpoint has to opt in to seeing deleted data, not remember to opt out.

With the fix, the deleted user signs up again and gets a new account id, the
same email can be deleted and reused several times (3 rows, 1 live), the race
leaves exactly 1 live account, and the member list shows only live users.

## Trade-offs
- **Database support.** MySQL has no partial indexes. Common workarounds: a
  generated column that is `email` for live rows and `NULL` for deleted ones,
  with a unique index on it (unique indexes allow many `NULL`s), or a
  `UNIQUE (email, deleted_marker)` where live rows use a fixed marker value.
- **Several rows per email.** Lookups by email that should see history
  (fraud checks, "you had an account before") must now handle many deleted
  rows plus at most one live row.
- **Default scoping is not enforcement.** A raw query can still forget the
  filter. A database view (`live_accounts`) or ORM default scope helps; for
  strict cases, move deleted rows to an archive table instead of flagging
  them.
- **Privacy.** Soft-deleted rows still hold personal data. Retention must be
  limited and the purge job monitored; a "delete my data" request may require
  anonymising the row right away rather than keeping it for 30 days.
- Normalise emails before storing them (see problem 020), or the index will
  treat `Asha@Example.com` and `asha@example.com` as different accounts.

## Run
```bash
python -m unittest -v test_solution.py
```
