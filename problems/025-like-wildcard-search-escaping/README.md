# 025 · Support agents searching for "_" got every customer in the database, and "bob_smith" also returned "bobXsmith"

## Problem
The internal support tool has a customer search box backed by
`GET /v1/customers?search=...`. Agents copy fragments of an email address
from a ticket and paste them in. Two kinds of complaints arrived:

- Searching for an address that contains `_` (very common: `bob_smith@...`)
  returned other customers too, e.g. `bobXsmith@...`. An agent nearly
  changed the wrong customer's payout details.
- A search for a single `_` or `%` returned the first page of *all*
  customers and, on the production table of several million rows, ran a full
  scan for many seconds, tying up a database connection.

The query was parameterised, so a security review had marked it "safe".

## Root cause
`naive_search` builds the pattern as `f"%{text}%"` and binds it as a
parameter to `email LIKE ?`. Binding stops SQL injection, but the database
still interprets LIKE metacharacters **inside the value**: `%` means "any
run of characters" and `_` means "any single character". So the user's own
`_` and `%` became wildcards. The tests show `"_"` and `"%"` matching all six
customers and `"bob_smith"` matching two.

## Solution
- `escape_like` escapes the escape character first (`\` → `\\`), then
  `%` → `\%` and `_` → `\_`. Order matters: escaping `\` last would double the
  backslashes we had just added.
- The query adds `ESCAPE '\'` so the database knows that `\%` means a literal
  percent sign. Without the clause, SQLite and PostgreSQL treat the pattern
  differently (PostgreSQL defaults to `\` as escape, SQLite has no default),
  so stating it makes the behaviour the same everywhere.
- Terms shorter than 2 characters (after stripping whitespace) are rejected,
  and results are capped at 50 rows, so an accidental near-empty search
  cannot scan and return the whole table.

## Trade-offs
- `LIKE '%term%'` can never use a normal B-tree index, even with escaping.
  For large tables use a trigram index (PostgreSQL `pg_trgm`) or a search
  engine; the escaping is still needed there for any LIKE fallback.
- Some agents relied on `_` as a wildcard. If wildcard search is a feature,
  offer it explicitly (for example `*` in the UI, translated to `%` after
  escaping) instead of leaking the database syntax.
- Prefix search (`term%`) is index-friendly and often enough for emails;
  consider it as the default and contains-search as an opt-in.

## Run
```bash
python -m unittest -v test_solution.py
```
