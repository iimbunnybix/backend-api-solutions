# 062 · A German seller's bulk price upload listed a €12.50 kettle at €1,250, and a €1,234.56 espresso machine at about €1.23

## Problem
A marketplace lets sellers update prices by uploading a CSV to
`POST /v1/sellers/{id}/prices:import`. The template was designed by the
US-based team with values like `1,234.56`.

A seller in Germany filled it in with their spreadsheet's default German
number format and uploaded about 3,000 rows. `12,50` (twelve euros fifty)
went live as 1250.00, so most of the catalogue became unsellable; the few
items over €1,000, written like `1.234,56`, went live at roughly €1.23 and
sold out within minutes before anyone noticed. The import reported
"3,000 rows imported, 0 errors".

## Root cause
`naive_parse_price` did `float(text.replace(",", ""))`. It assumes the comma
is always a thousands separator and the dot is always the decimal point.
In German (and many other European locales) it is the other way round, so:

- `12,50` → `1250` → 1250.0 (100 times too high),
- `1.234,56` → `1.23456` → 1.23456 (about 1,000 times too low),
- `0,99` → `099` → 99.0.

Nothing was rejected, because every mangled string was still a valid float.

## Solution
- The seller's **number format** is an explicit setting (`EN_FORMAT`:
  `1,234.50`, `DE_FORMAT`: `1.234,50`) chosen on the import screen, instead
  of being guessed from the data.
- `parse_price` checks the value against a strict pattern for that format:
  plain ASCII digits, or digit groups of **exactly three** separated by the
  group separator, plus an optional decimal part of at most two digits. That
  is what rejects a German `12,50` uploaded as English format: `,50` is not a
  valid thousands group. Signs, exponents (`1e3`), `nan` and non-ASCII digits
  are rejected too.
- Only after validation are group separators removed and the decimal
  separator turned into `.`, and the result is a `Decimal`, so `0,10` stays
  exactly `0.10`.
- `import_prices` collects errors per SKU and imports only the rows that
  parsed, so the seller gets a list of bad rows with an example of the
  expected format instead of a silent wrong price.

## Trade-offs
- Some values are valid in both formats with different meanings: `1,234` is
  1234 in English and 1.234 in German. Strict grouping cannot detect that; the
  declared format decides. A sanity check against the current price (for
  example, block changes above 10x without confirmation) is a good second
  line of defence.
- Only two formats are supported. Others exist: Swiss `1'234.50`, French
  `1 234,50` with a (narrow) no-break space, and Indian lakh grouping
  `1,23,456.00`, which this strict three-digit rule rejects. Use a locale
  library (for example Babel) if many locales must be supported.
- A machine-to-machine API should avoid locales entirely: accept JSON
  numbers as strings in one canonical format (`"1234.56"`) or integer minor
  units (`123456`), and do localisation only in the UI.

## Run
```bash
python -m unittest -v test_solution.py
```
