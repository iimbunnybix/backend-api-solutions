# 026 · Push notifications from customers with Hindi names or emoji in their message silently stopped arriving

## Problem
A food-delivery app sends a push notification to the restaurant for every
customer chat message, with a preview like `Priya: नमस्ते, आपका ऑर्डर...`.
The push gateway accepts a limited number of **bytes** for the preview, so
the notification service cut the text to fit. After a release that added
Hindi and Tamil UI, restaurant owners reported that some messages never
produced a notification. Logs showed `UnicodeDecodeError` in the
notification worker for a few percent of messages; every one of them
contained non-ASCII text or emoji. A first hotfix used
`decode(errors="ignore")`: the crash stopped, but previews now showed
consonants without their vowel signs and 👍🏽 turned into a plain yellow 👍.

## Root cause
`naive_truncate` slices the UTF-8 **bytes** at the limit and decodes them.
In UTF-8 a character takes 1 to 4 bytes (Devanagari letters take 3, most
emoji 4), so a byte cut can land inside a character; decoding the half
character fails. Even a cut on a character boundary can split one visible
symbol, because many symbols are several code points: a letter plus a
combining accent or vowel sign, an emoji plus a skin-tone modifier, or
several emoji glued with a zero-width joiner (👨‍👩‍👧 is five code points, 18
bytes).

## Solution
`truncate_utf8(text, max_bytes)`:

1. If the text already fits, return it unchanged.
2. Move the byte cut back while the next byte is a UTF-8 continuation byte
   (`0b10xxxxxx`), so the cut is between whole characters.
3. Move the cut back further while the character after it belongs to the one
   before it (combining marks `Mn`/`Mc`/`Me`, variation selectors, skin
   tones, the zero-width joiner) or the character before it is a joiner.

The result is always a prefix of the original, never longer than the limit
(the tests check every limit from 0 to the full length), and never ends
half-way through a symbol.

## Trade-offs
- This is a practical subset of Unicode's grapheme cluster rules (UAX #29),
  not the full algorithm; Python's standard library has no grapheme
  segmentation. Flags (pairs of regional indicators) and Devanagari
  conjuncts joined by a virama (e.g. `स्ते`) can still be split. Use the
  `regex` package's `\X` or ICU if those matter.
- Cutting can remove a few more bytes than strictly needed, so the preview
  can be up to one symbol shorter than the limit.
- If the gateway counts characters or UTF-16 units instead of bytes (some
  SMS providers do), truncate in that unit instead; the boundary rules stay
  the same.

## Run
```bash
python -m unittest -v test_solution.py
```
