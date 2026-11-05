# 030 · "My orders" showed only an error for every customer who had used the new click-and-collect option

## Problem
A grocery chain's order-tracking client parses JSON from the orders API
(`GET /customers/{id}/orders`) and renders the customer's order history. The
backend team launched click-and-collect and added one new order status,
`READY_FOR_PICKUP`. They treated it as an additive, non-breaking change: no
field was renamed or removed. Within an hour, support had hundreds of chats
saying "My orders" showed only an error, exactly for customers who had placed a
click-and-collect order. Those customers couldn't see their pickup code. Many
waited at the store counter while staff looked up orders by phone number. The
store app could only be fixed by a release and app-store review, which took
days.

## Root cause
The client mapped `status` with a **closed enum**: `OrderStatus(raw)` in
`naive_parse_order`. Python's `Enum` raises `ValueError` for any value it
doesn't know. Many strict deserializers do the same by default: Jackson
fails unless `READ_UNKNOWN_ENUM_VALUES_USING_DEFAULT_VALUE` is enabled, and
Swift `Codable` enums throw on an unknown raw value.

Two things made it worse:

- The whole list was parsed in one go, so in a 50-order history, one
  `READY_FOR_PICKUP` order made all 50 fail (`naive_parse_order_list`).
- Adding a value to an enum is backward compatible for the **server**, but not
  for a client that was written assuming the set of values is fixed. Old app
  versions stay installed for months.

This differs from problem 014, where a field was renamed. Here the schema did
not change at all: a new *value* appeared in an existing field.

## Solution
A **tolerant reader** for enum fields (`parse_status`, `parse_order`):

1. Known strings map to their member. Any other string maps to
   `OrderStatus.UNKNOWN`, and the original text is kept in `raw_status` for
   logs and support. The whole 50-order history now loads.
2. `UNKNOWN` is the client's own placeholder. The server cannot select it:
   a server that sends `"UNKNOWN"` is counted as unknown too.
3. Non-strings (`null`, numbers, lists) still raise. They are a broken
   payload, not a newer server, and hiding them would mask real bugs.
4. Every unknown value is counted per field and value
   (`UnknownValueCounter`), so a dashboard shows "client version X sees
   `READY_FOR_PICKUP`" instead of a crash rate.
5. Business rules treat `UNKNOWN` conservatively. The UI shows a neutral
   "In progress" label, and `can_cancel` uses an allow-list, so a status the
   client doesn't understand never offers a "Cancel" button for an order that
   may already be packed.

## Trade-offs
- `UNKNOWN` loses meaning: an old client can't show "ready for pickup" or the
  pickup code until it is updated. Tolerance prevents the crash. It doesn't
  make the feature work. Sending a human-readable `status_label` from the server
  alongside the code would let old clients show something useful.
- Every `match`/`if` on the enum needs a deliberate `UNKNOWN` branch. Choosing
  the safe default (no cancel, no refund button) is a product decision per
  rule, and a wrong default can be as bad as the crash.
- Silently tolerating values can hide real typos from the server, such as
  `"SHIPED"`. The counter and an alert on new unknown values are what keep
  that visible.
- Servers should still treat new enum values as a contract change. Announce
  them, gate them on client version, or map them to an old value
  (`READY_FOR_PICKUP` → `SHIPPED`) for old clients until they upgrade.
- Matching is exact and case-sensitive (`"shipped"` is unknown). Normalising
  case would be more forgiving, but it could hide a server bug.

## Run
```bash
python -m unittest -v test_solution.py
```
