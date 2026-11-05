# 021 · Customers clicking an order in the web app saw someone else's order, but the mobile app was fine

## Problem
An e-commerce platform switched order IDs from an auto-increment column to a
Snowflake-style 64-bit generator (time bits + machine bits + sequence), so new
IDs were around 1.9 × 10¹⁸. The orders API kept returning them as JSON
numbers. Within a day support had tickets from web users who clicked their
order and saw another customer's order, and two refunds were issued against the
wrong order. The Android and iOS apps (64-bit integer parsing) showed the right
data, which sent the investigation in the wrong direction for hours.

## Root cause
JavaScript's `JSON.parse` turns every number into an IEEE-754 double, which
stores integers exactly only up to `Number.MAX_SAFE_INTEGER` = 2⁵³ − 1
(9,007,199,254,740,991). Above that, doubles are spaced more than 1 apart; at
1.9 × 10¹⁸ the spacing is 256. In the test, order `1891234567890123457` arrives
in the browser as `1891234567890123520`, and its neighbour
`1891234567890123458` rounds to the *same* value. The web app then sent that
rounded ID back in `GET /orders/{id}` and refund calls, and in production a
rounded ID could match a real, different order. Nothing errored: the JSON was
valid, only the client's number type was too small.

## Solution
- **Send IDs as strings.** `serialize_order` writes every ID field
  (`id`, `customer_id`) as a decimal string; every JSON parser keeps strings
  byte-for-byte. Money and counts stay numbers, so only ID handling changes.
  This is what large public APIs do (e.g. Twitter's `id_str`, Stripe and
  Google APIs use string IDs, and protobuf's JSON mapping encodes int64 as a
  string).
- **Parse incoming IDs strictly.** `parse_id` accepts decimal strings from new
  clients and exact integers from old clients during the migration, and
  rejects floats (a float ID has probably already been rounded), booleans,
  and strings that Python's `int()` would quietly accept (`" 12"`, `"+12"`,
  `"1_2"`, Arabic-Indic digits). IDs must be in `1..2⁶³−1` to fit the BIGINT
  column.
- `parse_like_javascript` reproduces the browser's behaviour in tests, so the
  bug is caught in CI rather than by customers.

## Trade-offs
- Changing a field's type from number to string is a breaking API change;
  ship it as a new API version or add a parallel `id_str` field first and
  migrate clients (see problem 014).
- Strings sort lexically (`"10" < "9"`); clients must not sort by ID strings.
  Sort by a timestamp or server-provided order instead.
- Alternatives: keep IDs below 2⁵³ (e.g. a 53-bit Snowflake layout, which
  limits machines or time range), or have clients use a big-integer JSON
  parser — fragile, because every client and every intermediate tool (log
  viewers, `jq`, spreadsheets) must get it right.
- The strict parser also rejects legacy clients that send IDs as floats; that
  is intended, because those IDs cannot be trusted.

## Run
```bash
python -m unittest -v test_solution.py
```
