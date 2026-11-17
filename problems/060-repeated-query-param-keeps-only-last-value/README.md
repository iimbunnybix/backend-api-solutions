# 060 · The support dashboard's "pending or failed" order filter showed only failed orders, and stuck pending payments went unnoticed

## Problem
An order service exposes `GET /orders?status=...&customer_id=...&limit=...`.
The support team's dashboard added a multi-select status filter; picking
"pending" and "failed" sends `?status=pending&status=failed`, the standard
way HTML forms and most HTTP clients encode several values for one field.

The list looked plausible, so for two weeks nobody noticed that it contained
only failed orders. Orders stuck in `pending` (customer charged, confirmation
never sent) were exactly what support was meant to chase, and they piled up
until customers started filing chargebacks. Swapping the order of the two
checkboxes flipped the result to only pending orders, which made the bug
look intermittent.

## Root cause
The handler parsed the query with `dict(urllib.parse.parse_qsl(query))`.
`parse_qsl` returns every pair, but building a `dict` from them keeps only the
**last** value for each key. `status=pending&status=failed` became
`{"status": "failed"}` with no error. Unknown keys were also ignored, so a
typo like `stauts=paid` returned every order instead of an error.

## Solution
`parse_filters` parses against a small schema for the endpoint:

- `parse_qs(..., keep_blank_values=True)` keeps **every** value of a key as a
  list, in order.
- **List parameters** (`status`) keep all values and mean "any of these";
  each value is checked against the allowed statuses.
- **Single parameters** (`customer_id`, `limit`) that appear more than once
  are rejected with `BadRequest` (HTTP 400). Different proxies, WAFs and
  frameworks pick the first or the last copy, and that disagreement is the
  basis of HTTP parameter pollution attacks, so refusing is safer than
  picking one.
- **Unknown parameters** and blank values are rejected, so a typo fails
  loudly instead of widening the result.
- `limit` must be ASCII digits between 1 and 100.

In the tests `status=pending&status=failed` returns all four matching orders
(the original code returned two), and swapping the parameter order no longer
changes the result.

## Trade-offs
- Rejecting unknown parameters can break clients that send extra tracking
  parameters (`utm_source`, cache busters). An allowlist of ignorable names
  is a common compromise.
- Some APIs use `status=pending,failed` or `status[]=pending` instead of
  repeated keys. Pick one convention, document it in the API spec, and parse
  exactly that.
- Frameworks with typed query models (FastAPI `list[str]`, Django
  `QueryDict.getlist`) solve this declaratively; the bug usually comes back
  when someone flattens the query into a plain dict "for convenience".

## Run
```bash
python -m unittest -v test_solution.py
```
