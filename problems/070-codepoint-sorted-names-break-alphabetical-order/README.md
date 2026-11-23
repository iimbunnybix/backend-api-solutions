# 070 · Customers named "Émile" or "anna" were missing from the A-Z contact directory and support could not find them

## Problem
The CRM's contacts API returns customers sorted by name, and the support
console shows them as an A-Z directory with "jump to letter" tabs. The
company sells in France, Germany and Scandinavia, and a sizeable share of
names start with an accented letter, and many imported records are all
lower-case.

Support agents reported that "Émile Durand" was not under **E**, and that
"anna Berg" was not under **A**. Both were at the very end of the list,
after "Zoe", on a page nobody scrolled to. Agents concluded the customer
did not exist and opened duplicate records. Paging through the list in
order also broke when a single page mixed the two groups.

## Root cause
The endpoint used `sorted(names)` (`naive_sort_names`). Python compares
strings by **Unicode code point**, not alphabetically:

- every capital letter (`A`-`Z`, 65-90) is before every lower-case letter
  (`a`-`z`, 97-122), so `["Zoe Adams", "anna Berg"]` stays in that order;
- accented letters such as `É` (201) and `Ø` (216) come after `z` (122), so
  `["Eva Lind", "Zoe Adams", "Émile Durand"]` is the "sorted" result.

The A-Z tabs used the raw first character (`naive_first_letter`), so
"Émile" got its own `É` tab that the UI did not render.

## Solution
`sort_names` sorts by a **collation key** (`collation_key`) with three
levels, compared in order:

1. **Base letters**: the name is decomposed with NFKD (so `É` becomes `E` +
   a combining accent, and the precomposed and decomposed spellings of a
   name are equal), the combining marks are dropped, and the result is
   case-folded (`ß` becomes `ss`). A few letters that are not "letter +
   accent" in Unicode (`ø`, `ł`, `æ`, `þ`...) are mapped by a small table.
2. **Accents and case together** (the case-folded original), so "Emile"
   sorts before "Émile".
3. **The exact original string**, so the order is total and deterministic
   regardless of input order.

The directory tabs use the first base letter (`first_letter`), so "Émile"
is under **E** and "Øster" under **O**; names that do not start with a
Latin letter go under `#`. The same key must be used for keyset pagination
cursors, or pages will skip names.

## Trade-offs
- This is a reasonable default for Latin-script names, not the full Unicode
  Collation Algorithm. Real locales disagree: Swedish sorts `Ö` after `Z`,
  German phone books sort `ä` as `ae`, Spanish once treated `ll` as one
  letter. If locale-correct order matters, use ICU (`PyICU`) or the
  database's ICU collations with the user's locale.
- `locale.strxfrm` is in the standard library but depends on which locales
  are installed on each server, so two hosts could return different orders
  for the same query.
- Sorting in Python only works when the whole result fits in memory. For
  paginated lists the database must sort with the same rules, either via a
  collation or via a stored, indexed `sort_key` column computed with this
  function at write time.
- Ignoring accents on the first level means "Muller" and "Müller" are
  neighbours, which is what users expect from a directory, but it is not a
  search: matching the two needs its own normalisation.

## Run
```bash
python -m unittest -v test_solution.py
```
