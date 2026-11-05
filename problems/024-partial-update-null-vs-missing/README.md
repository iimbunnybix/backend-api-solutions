# 024 · Users who changed only their display name in the app found their phone number and bio erased

## Problem
The profile API exposes `PATCH /v1/profile`. A new mobile release started
sending only the fields the user actually edited, e.g.
`{"display_name": "Ann"}`, instead of the whole profile. Within a day support
received hundreds of tickets: users who renamed themselves lost their phone
number (which also broke SMS login for some) and their bio. The emergency
hotfix stopped the data loss, but then users who tapped "Remove phone number"
saw the old number come back after every refresh.

## Root cause
The handler copied every known field with `body.get(name)`. In Python (and
most JSON decoders mapped into structs with default values) a field that is
**absent** and a field that is **`null`** both come out as `None`, so "not
sent" overwrote the stored value with nothing. The hotfix skipped every
`None`, which fixed the wipe but made "sent as `null`, please clear it"
indistinguishable from "not sent": a field could never be cleared.

The tests show both: renaming through `original_patch_profile` sets `phone`
and `bio` to `None`, and `skip_none_patch_profile` ignores `{"phone": null}`.

## Solution
Model the three cases separately, following JSON Merge Patch (RFC 7396):

| In the body | Meaning |
|---|---|
| field absent | keep the stored value |
| `"field": null` | clear it (only if the field is nullable) |
| `"field": value` | set it, after validation |

- A unique `MISSING` sentinel (`body.get(name, MISSING)`) separates "absent"
  from `None`; `parse_patch` returns only the fields that were sent.
- Each field has a `FieldRule`: exact type (so `true` is not a string),
  nullability (`display_name` cannot be cleared) and max length.
- Unknown fields (`"phnoe"`, or `"user_id"`) are rejected with 422 instead of
  being silently ignored, which also blocks mass assignment of server-owned
  fields.
- The whole body is validated before anything is applied, so one bad field
  never leaves a half-updated profile; the stored dict is never mutated.

## Trade-offs
- Merge Patch cannot express "set this field to literal `null`" for data
  where `null` is a real value, nor edit inside arrays. JSON Patch (RFC 6902,
  `[{"op": "remove", "path": "/phone"}]`) is explicit but heavier for clients.
- Rejecting unknown fields breaks older or newer clients that send extra
  fields; some APIs log and ignore instead. Rejecting is safer for writes.
- Typed frameworks need the same idea: Pydantic's `model_fields_set` /
  `exclude_unset=True`, `Optional<Optional<T>>` or a dedicated `Undefined`
  type in other languages. Using a plain "optional with default None" model
  reintroduces this bug.
- Concurrent PATCHes still last-write-win per field; add an `If-Match` ETag
  (see problem 007) if two editors may change the same field.

## Run
```bash
python -m unittest -v test_solution.py
```
