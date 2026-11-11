# 048 · The app kept showing a ₹19,990 price for a ₹1,999 product hours after the typo was fixed

## Problem
A retail catalog API serves product JSON to the mobile app, which caches it
and revalidates with `If-Modified-Since` on every screen view. Responses carry
`Last-Modified` taken from the row's `updated_at`.

A merchandiser saved a price of ₹19,990 instead of ₹1,999 and corrected it
half a second later. Some app users who had opened the product page in that
half second kept seeing ₹19,990 for the rest of the day, while everyone else
saw ₹1,999. Support could not reproduce it: a fresh install always showed the
right price. Several users abandoned checkout, and a few posted screenshots.

In `same_second_correction` (typo saved at .2 s, app fetch at .4 s, correction
at .7 s, revalidation 60 s later) the original handler returns
`304 Not Modified` and the app keeps showing **19,990**.

## Root cause
HTTP dates have **one-second resolution**. The typo and the correction both
happened inside the same second, so both versions had the same
`Last-Modified` value. When the app revalidated with that date, the server
compared `int(updated_at) <= If-Modified-Since`, found them equal and answered
`304`. A date is only a correct validator if the resource cannot change again
within the second it names, and the API had no other validator to fall back to.

## Solution
`fixed_get` stops relying on the timestamp alone:

1. **Strong ETag from the row version** (`"v2"`), bumped on every update, so
   two changes in the same millisecond still get different validators.
2. **`If-None-Match` takes precedence**: when it is present,
   `If-Modified-Since` is ignored, as RFC 9110 (section 13.2.2) requires.
3. **`Last-Modified` is only sent once its second is over.** While the clock is
   still in the modification second, another update could get the same date, so
   the date is withheld and an `If-Modified-Since` for that second is not
   trusted. Clients that only use dates (some proxies) then get a full `200`
   instead of a wrong `304`.

With the same sequence the app shows **1,999** after revalidation. Unchanged
products still get `304` by ETag, and by date once the second has passed.

## Trade-offs
- The version column must be bumped by every write path, including bulk SQL
  jobs; an update that skips it is invisible to clients. A database trigger or
  a hash of the stored row avoids relying on application code.
- Withholding `Last-Modified` during the modification second costs a few
  extra full responses for date-only clients right after an edit; that is far
  cheaper than serving a wrong price.
- The rule assumes one clock. With several app servers whose clocks differ, a
  server running behind could think the second is over when it is not; keep
  clocks synced (devops problem 016) or use ETags only.
- Caches holding the stale copy for a fixed `max-age` are not fixed by
  validators; prices that must be fresh need a short `max-age` or explicit
  purges.

## Run
```bash
python -m unittest -v test_solution.py
```
