# 020 · Support found customers with two accounts, and a user registered "Admin" right next to "admin"

## Problem
A consumer app's sign-up API (`POST /accounts`) let people pick a username
and register with an email address. Over a few months support kept seeing
the same pattern: a customer could not find their order history because they
had signed up again as `Alice` after first registering as `alice`, password
resets went to the "wrong" account, and loyalty points were split between
two profiles. One user registered `Admin` and posted in community forums
looking like the real `admin` account. Some duplicates looked identical on
screen: one phone keyboard produced full-width letters (`ａｌｉｃｅ`), and
names with accents could be stored either composed (`é`, one code point) or
decomposed (`e` + combining accent).

## Root cause
Uniqueness was enforced by a `UNIQUE` index on the **raw** strings. To the
database `Alice`, `alice`, `ａｌｉｃｅ` and the two forms of `josé` are all
different byte sequences, so each insert succeeded. The `ReproduceProblem`
tests create two accounts in each of these cases. Application-level
`lower()` checks before insert would have helped with ASCII case only, and a
check-then-insert also races under concurrent sign-ups.

## Solution
`canonical_identifier()` builds the key used for uniqueness and login:

1. Strip surrounding whitespace.
2. **NFKC** normalisation: composes accents into a single code point and
   maps compatibility characters (full-width letters, ligatures) to their
   plain form, so `ａｌｉｃｅ` becomes `alice`.
3. **casefold()**: Unicode-aware case-insensitive form, stronger than
   `lower()` (`Straße` and `STRASSE` both become `strasse`).
4. NFKC again, because case folding can produce sequences that need
   re-normalising.

The table keeps the display value the user typed and stores the canonical
key in a separate column with the `UNIQUE` index. The insert itself is the
check, so concurrent sign-ups for `Bob` and `bob` cannot both win. Login
looks users up by the same key. Usernames are also rejected when empty, longer
than 32 characters, or containing Unicode "C" category characters (control
and invisible format characters such as the zero-width space). Emails get
the same invisible-character check, must not be blank, and must contain
exactly one `@` with text on both sides.

## Trade-offs
- NFKC + casefold does not catch **confusables** from different scripts: the
  Cyrillic `а` looks like the Latin `a` but is a different letter. Blocking
  those needs the Unicode confusables data (UTS #39) or a rule allowing only
  one script per username.
- Email local parts are case-sensitive by the RFC, but in practice almost
  all providers treat them case-insensitively, so folding the whole address
  avoids far more duplicates than it causes. Provider-specific rules (dots
  and `+tags` in Gmail) are deliberately not applied.
- Existing data needs a migration: backfill the key column, find accounts
  that collide, and merge or rename them before adding the `UNIQUE` index.
- The canonical form depends on the Unicode version of the Python build; a
  later version can map a few new characters differently, so record which
  version produced the stored keys.

## Run
```bash
python -m unittest -v test_solution.py
```
