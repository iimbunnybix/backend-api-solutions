# 067 · Customers who set their browser to German kept getting French product pages, and Swiss users got English

## Problem
An online shop serves its pages in English, German, French and Brazilian
Portuguese. It picks the language from the browser's `Accept-Language`
header so first-time visitors see their own language without choosing.

After launching in Germany and Switzerland, support received complaints in
German that the site was "in French", and Swiss customers said it was "only
in English". Bounce rate for German-speaking visitors was far higher than
for other markets, and some customers left the checkout because they could
not read the terms.

Typical headers from those customers:

- `fr;q=0.1, de` — a German user who once added French as a last resort.
  Many browsers keep the order in which languages were added, not the order
  of preference, and express preference only through `q`.
- `fr;q=0, de` — French explicitly marked as not acceptable.
- `de-CH, en;q=0.5` — Swiss German, with English as a fallback.

## Root cause
`naive_pick_language` took the **first** tag in the header and ignored the
quality values. In `Accept-Language` the order of entries does not express
preference; the `q` weight does (RFC 9110, section 12.5.4). `q=0` means
"not acceptable", yet the code served it. And a regional tag such as
`de-CH` was compared only by exact string, so it never matched the shop's
`de` and fell through to the default English.

## Solution
`pick_language` does what the RFC describes:

1. **Parse every entry** with its q-value (`parse_accept_language`). A
   missing `q` means 1. Entries with an invalid q (not a number, outside
   0..1, more than three decimals) or unknown parameters are skipped instead
   of guessed.
2. **Rank** by q, highest first. Python's sort is stable, so entries with
   the same q keep the order the user listed them.
3. **Exclude refusals**: any tag with `q=0` is never served, even through
   a fallback.
4. **Match** each tag exactly (case-insensitively, `PT-br` -> `pt-BR`), then
   by its primary language (`de-CH` -> `de`), and only then try the next tag.
5. `*` accepts anything and maps to the site default unless the default was
   refused. With no header or no match, the default (`en`) is used.

## Trade-offs
- Primary-language fallback is a simplification of RFC 4647 "lookup"
  matching. It cannot express "fall back from `pt-PT` to `pt-BR`"; shops
  that care about such pairs need an explicit fallback table.
- The header is only a hint. Always let users pick a language and remember
  the choice (cookie or account setting), and put the language in the URL
  (`/de/...`) so links and search engines get a stable page.
- If the response depends on `Accept-Language`, send `Vary: Accept-Language`
  or caches and CDNs will serve one user's language to everyone. Redirecting
  `/` to `/de/` once avoids fragmenting the cache on every header variant.
- Malformed entries are dropped rather than rejected with a 400: a broken
  language header should not stop someone from shopping.
- Libraries such as Babel (`negotiate_locale`) implement this if
  third-party dependencies are allowed.

## Run
```bash
python -m unittest -v test_solution.py
```
