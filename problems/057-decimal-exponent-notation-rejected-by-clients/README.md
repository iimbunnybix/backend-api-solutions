# 057 · Wallet balances of exactly $10 or $100 showed "Amount unavailable" in the mobile app after a serialiser clean-up

## Problem
A wallet API returns money as JSON strings built from Python `Decimal`
values (`{"balance": "10.55", "currency": "USD"}`), so clients never see
binary-float rounding. A small refactor wanted to stop sending noise such as
`"10.5000"` from rate calculations and changed the serialiser to
`str(amount.normalize())`.

After the release, users whose balance was a round number (exactly $10.00,
$100.00, $2,000.00 after a top-up) saw "Amount unavailable" in the iOS and
Android apps, and the "Top up" button disappeared because the app thought
the balance failed to load. Balances with cents still worked, which is why
the test suite and code review missed it. The pricing page for metered API
calls broke the same day: the per-call price of `0.00000015` was being sent
as `"1.5E-7"`.

## Root cause
`Decimal.normalize()` removes trailing zeros by raising the exponent, and
`str()` then uses scientific notation whenever the exponent is positive:

| Value | `str(value.normalize())` |
|---|---|
| `Decimal("10.00")` | `"1E+1"` |
| `Decimal("100.00")` | `"1E+2"` |
| `Decimal("10.55")` | `"10.55"` |
| `Decimal("0.00000015")` | `"1.5E-7"` (even plain `str()` does this) |

`str(Decimal)` also switches to exponent notation for very small values
(six or more zeros between the point and the first digit), with or without `normalize()`.
The mobile apps validate amounts with the pattern `^-?\d+(\.\d+)?$`, so
every exponent string was rejected. The serialiser's output format depended
on the *value*, not on the currency.

## Solution
`format_amount(amount, currency)` makes the format a property of the
currency:

- **Fixed decimal places per currency** (ISO 4217 minor units: USD/EUR/INR 2,
  JPY 0, KWD 3). `quantize()` pads `10` to `10.00` and `1E+2` to `100.00`.
- **No exponent, ever**: `format(amount, "f")` always writes plain digits,
  unlike `str()`.
- **No silent rounding**: the `Inexact` signal is trapped, so `10.005 USD`
  raises `AmountFormatError` instead of quietly becoming `10.00` or `10.01`.
  An amount with fractions of a cent is a bug upstream, and should fail
  loudly where it is created.
- Unknown currencies, `NaN` and `Infinity` are refused, and `-0.00` is sent
  as `0.00`.
- `format_unit_price` uses a fixed 8 decimal places for per-unit prices, so
  `1.5E-7` is sent as `"0.00000015"`.

The tests check every formatted value against the same validator pattern the
apps use, so a regression fails in CI rather than in the app stores.

## Trade-offs
- Refusing to round is strict: callers must round explicitly (with a named
  rounding mode) before formatting. That is deliberate for money, but a
  display-only field could choose to round instead.
- The currency table must be kept in sync with the currencies you support;
  a library such as Babel carries the full ISO 4217 list.
- Sending JSON *numbers* instead of strings would avoid the pattern check but
  bring back float precision problems in JavaScript and many JSON parsers, so
  strings are kept.
- An alternative is integer minor units (`"balance_minor": 1000`), which
  cannot have exponent or rounding issues at all, but every client must know
  each currency's exponent to display it; changing the contract also needs
  API versioning (see problem 014).

## Run
```bash
python -m unittest -v test_solution.py
```
