# 022 · Patients in India arrived five and a half hours early for video consultations booked through the app

## Problem
A telehealth booking API serves clinics in several countries. Doctors publish
slots, patients book them in the web and mobile apps, and reminders are sent
from the backend. After the service expanded from the UK to India, clinics
reported patients joining calls hours early and then missing the real slot;
staff in New York looking at the same booking saw yet another time. Missed
consultations were refunded, and the clinics' trust in the scheduling tool
dropped.

## Root cause
The backend kept times as *naive* datetimes: wall-clock digits with no
timezone, which the team "knew" meant UTC. The API returned them as
`"2026-03-10T09:00:00"`. ISO-8601 (and JavaScript's `Date`, and most mobile
date libraries) read a date-time without an offset as **local time on the
device**. So:

- A patient in India (UTC+05:30) saw 09:00 IST, while the slot was 09:00 UTC
  = 14:30 IST: 5 h 30 m early.
- A New York user (UTC−05:00 in winter) saw 09:00 EST, so two clients
  disagreed about the same booking by 10 h 30 m.

While UK clients were the only users the bug was invisible for half the year,
because UK winter time equals UTC. Requests had the mirror problem: a naive
time sent *by* a client was stored as if it were UTC.

## Solution
Make every timestamp at the API boundary an exact instant:

- `require_aware` / `to_utc` refuse naive datetimes instead of guessing a
  zone, so code that builds a naive value fails in tests, not in production.
- `parse_api_timestamp` accepts ISO-8601 with an offset (`+05:30`, `-05:00`,
  or `Z`), rejects strings without one with `NaiveDatetimeError` (HTTP 400),
  and converts to UTC for storage.
- `format_api_timestamp` / `serialize_appointment` always emit UTC with the
  offset written out: `"2026-03-10T09:00:00+00:00"`. Clients convert to their
  own zone for display; in the test the India client sees 14:30 and the New
  York client sees 04:00, both the same instant.
- `parse_like_client` reproduces device behaviour in tests, so a regression
  shows up as a failing test rather than missed appointments.

`"Z"` is translated to `"+00:00"` before parsing because
`datetime.fromisoformat` only accepts `Z` from Python 3.11.

## Trade-offs
- Storing UTC is right for instants (a call at a fixed moment). For *future
  local* events such as "every Monday 09:00 at the clinic", store the wall
  time plus an IANA zone name (`Asia/Kolkata`) and resolve it when needed,
  because DST rules change and a stored UTC instant would drift.
- Rejecting naive input breaks old clients that never sent offsets; roll it
  out behind an API version or log-only mode first (see problem 014).
- The tests use fixed offsets so they do not depend on the machine's timezone
  database; production code should use `zoneinfo.ZoneInfo` for real zones.
- Database columns should be timezone-aware too (e.g. PostgreSQL
  `timestamptz`), otherwise the same bug comes back at the storage layer.

## Run
```bash
python -m unittest -v test_solution.py
```
