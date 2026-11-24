# 072 · Every order from the new app version failed with 415 Unsupported Media Type, though the JSON body was valid

## Problem
A food-delivery company's mobile app posts orders as JSON to
`POST /orders`. A new app release replaced the hand-written HTTP code with a
popular networking library. Within minutes of the staged rollout, every order
placed from the new version failed with **415 Unsupported Media Type**.
Old versions kept working, so the overall error rate rose slowly with the
rollout and the alert fired only after the release reached a large share of
users. Customers saw "Something went wrong" at the very last checkout step
and abandoned their carts until the rollout was paused.

The request bodies were byte-for-byte identical. Only one header changed:

| `Content-Type` sent | original check | fixed check |
|---|---|---|
| `application/json` (old app) | accepted | accepted |
| `application/json; charset=utf-8` (new app) | **415** | accepted |
| `Application/JSON` | **415** | accepted |
| `application/merge-patch+json` | **415** | accepted |
| `application/json; charset=iso-8859-1` | **415** | 415, with the reason "JSON must be UTF-8" |
| `text/plain` | 415 | 415 |

## Root cause
The handler compared the whole header to a string:
`content_type == "application/json"` (`naive_is_json`). But a `Content-Type`
value is a **media type**, not a fixed string (RFC 9110, section 8.3.1):

- it may carry parameters after `;`, such as `charset=utf-8`, with optional
  whitespace and optionally quoted values;
- the type, subtype and parameter names are **case-insensitive**.

Many HTTP clients add `; charset=utf-8` automatically when they serialise a
string body, so the exact match broke as soon as the client library changed.
This is not the header-*name* casing bug of problem 042; here the header
arrived, but its *value* was compared as plain text.

## Solution
`check_json_content_type` parses the value instead of comparing it:

1. `parse_media_type` splits on `;`, lower-cases and validates the
   `type/subtype`, and turns the parameters into a dict with lower-case names
   and unquoted values.
2. `is_json_essence` accepts `application/json` and the `+json` structured
   syntax suffix (`application/merge-patch+json`, `application/vnd.api+json`),
   but not look-alikes such as `application/jsonp`.
3. A `charset` parameter is allowed only if it is UTF-8. JSON exchanged between
   systems must be UTF-8 (RFC 8259), so a body labelled `iso-8859-1` gets a 415
   that says why, instead of being decoded into garbled text.
4. A body that is not valid UTF-8 JSON gets a 400, not a 500.

## Trade-offs
- `application/json` officially defines no `charset` parameter, so a server
  could just ignore it. Rejecting non-UTF-8 values is stricter but catches
  clients that really send Latin-1 bytes.
- Accepting every `+json` type is convenient, but an endpoint that needs to
  tell `merge-patch+json` from `json-patch+json` must check the exact type.
- This small parser does not handle a `;` inside a quoted parameter value.
  `email.message.Message` in the standard library, or a framework's request
  parser (most frameworks already do this), handles the full grammar.
- Being lenient on input is about accepting valid variations of the same
  format. Guessing the format of a body without a `Content-Type` is still
  rejected.

## Run
```bash
python -m unittest -v test_solution.py
```
