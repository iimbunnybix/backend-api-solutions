# 004 · Mobile activity feed shows the same items twice while scrolling, and some items never appear

## Problem
The activity feed API (`GET /events?page=N&size=20`, newest first) powers
infinite scroll in the mobile app. Support tickets report duplicated cards
when scrolling during busy periods, and the client-side "unread" counter
occasionally disagrees with what users can find in the feed. Deep pages are
also the slowest queries in the database's slow log.

## Root cause
The endpoint uses `LIMIT size OFFSET page*size`. An offset is a *count of
rows from the top*, not a position, so it is only stable if nothing changes
between requests:

- **Inserts at the head shift rows down.** In the test, 5 events are added
  while the user reads page 0; page 1 then repeats exactly those 5 rows.
- **Deletes shift rows up.** Deleting 3 newer events makes page 1 start 3
  rows later, so 3 live events are never shown to that user.
- **Cost grows with depth.** The database must walk and discard `OFFSET`
  rows on every request, so page 500 reads 10,000 rows to return 20.

## Solution
**Keyset (cursor) pagination** on the sort key `(created_at, id)`:

- Return an opaque `next_cursor` encoding the last row's `(created_at, id)`.
- The next page asks `WHERE (created_at, id) < (?, ?) ORDER BY created_at
  DESC, id DESC LIMIT size+1`. `id` breaks ties, so rows sharing a timestamp
  are neither duplicated nor skipped (tested with 7 rows per timestamp).
- Fetching `size+1` rows tells us whether another page exists without a
  `COUNT(*)`, and avoids an empty trailing page.
- A composite index matching the `ORDER BY` turns each page into an index
  range seek (the test checks the query plan uses `idx_events_feed`), so
  page cost no longer depends on depth.
- Cursors are HMAC-signed and versioned; tampered, foreign-key or malformed
  cursors raise `InvalidCursor` (→ HTTP 400) instead of producing odd queries.

With inserts happening between every page, the test walks the full feed and
gets each of the 100 original events exactly once, in order.

## Trade-offs
- No "jump to page 37". Keyset gives next/previous navigation; if random
  access is a hard requirement, keep offset for shallow pages only.
- New rows inserted *above* the cursor are not shown in the current scroll;
  clients need a separate "new items" pull from the head (common UX anyway).
- The sort key must be unique and immutable. Sorting by a mutable column
  (e.g. `score`) can still move rows across the cursor.
- The cursor is signed, not encrypted: don't put anything secret in it.
  Rotating the signing key invalidates in-flight cursors (clients restart
  from page one).

## Run
```bash
python -m unittest -v test_solution.py
```
