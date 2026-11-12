# 050 · Every new order failed with "Duplicate entry '2147483647' for key 'PRIMARY'" while the orders table was only 70% full

## Problem
An e-commerce platform stores orders in a table whose primary key is a signed
32-bit `INT AUTO_INCREMENT` (a `SERIAL` in PostgreSQL behaves the same). The
largest value it can hold is **2,147,483,647**. The capacity dashboard
tracked the row count and alerted at 80% of that limit.

One afternoon every checkout began failing with a duplicate-key error on
`orders.id` (MySQL keeps handing out the maximum value once it is reached;
PostgreSQL reports that the sequence reached its maximum value). The
dashboard said the table was about 70% full, so the on-call team first looked
for a bad deploy. Checkout was down for hours while
the key column, and the order-item and payment columns pointing at it, were
widened under pressure.

In `simulate()` (id counter starting at 600,000,000, 400,000,000 rows, then
4,000,000 committed orders, 1,000,000 rolled-back orders and 200 bulk imports
of 10,000 rows per day), inserts fail on **day 186**. At that moment the table
holds only **70.9%** of 2,147,483,647 rows, so the row-count alert **never
fired**.

## Root cause
Ids are not rows. An auto-increment counter only moves forward:

- A rolled-back insert (declined payment, failed validation after the
  insert) has already taken its id, and it is never reused.
- Bulk inserts of unknown size reserve ids in doubling blocks (MySQL InnoDB
  does this in its default lock mode, and sequence caches in other databases
  waste ids similarly), so a 10,000-row import uses 16,384 ids.

In the simulation 8,276,800 ids are used per day for 6,000,000 new rows, so
27.5% of the ids are burned. Without that waste the limit would be reached
on day 257 instead of day 186. The monitor measured the wrong quantity, and a
threshold on a fraction gave no idea of how much *time* was left.

## Solution
1. **Monitor the id counter, not the row count.** `HeadroomMonitor` records
   the highest allocated id once a day (from `AUTO_INCREMENT` in
   `information_schema.tables`, or the sequence's `last_value`).
2. **Forecast time, not percentage.** It divides the remaining ids by the
   allocation rate over the last 7 days (`RATE_WINDOW_DAYS`), so a recent
   rise in traffic shows up immediately instead of being averaged over the
   table's lifetime.
3. **Alert with migration lead time.** It alerts when fewer than 90 days
   remain (`ALERT_LEAD_DAYS`), which is how long a careful online migration
   of a large table can take. In the simulation it alerts on **day 97**,
   **89 days** before inserts would fail.
4. **Check the whole migration.** `migration_problems` flags the key *and*
   every column that references it if they are not 64-bit, because a BIGINT
   `orders.id` still breaks inserts into an `INT` `order_items.order_id`. A
   signed BIGINT tops out at about 9.2 × 10^18, which no realistic workload
   reaches.

## Trade-offs
- The forecast assumes the recent rate continues. A sudden bulk backfill
  can burn months of ids in a day; also alert on the absolute fraction used
  (e.g. 75%) as a backstop.
- A 7-day window reacts to weekly growth, but it also reacts to one-off spikes;
  a longer window is calmer but slower to notice a real increase.
- Widening a column rewrites the table on many databases. Online schema
  change tools (or a new BIGINT column filled in the background and swapped
  in) avoid long locks but take time, which is why the lead time is large.
- Short-term workarounds exist (restarting the sequence at the negative
  range of a signed INT, or reducing bulk reservation waste) but only buy
  time; BIGINT from the start costs 4 extra bytes per row and index entry.

## Run
```bash
python -m unittest -v test_solution.py
```
