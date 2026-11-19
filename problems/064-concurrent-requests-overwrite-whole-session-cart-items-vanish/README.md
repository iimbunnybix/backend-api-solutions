# 064 · Items added to the cart vanished a second later whenever the product page was still loading in the background

## Problem
A web shop stores each visitor's session (cart, recently viewed products,
locale, applied coupon) as a single JSON blob in a key-value store. Every
request loads the blob at the start and writes the whole blob back at the
end.

The product page fires two calls almost together: `POST /cart/items` when
the shopper taps "Add to cart" and `POST /recently-viewed` from the page's
tracking script. Support received a steady stream of "I added it and it
disappeared" tickets, mostly from mobile users on slow networks, and
conversion on the product page dropped. The cart service logs showed every
add succeeding.

## Root cause
Each request saved its **whole** copy of the session, including keys it had
never touched. With this interleaving:

1. add-to-cart loads `{cart: [tea]}`; recently-viewed loads `{cart: [tea]}`,
2. add-to-cart saves `{cart: [tea, kettle]}`,
3. recently-viewed saves `{cart: [tea], recently_viewed: [toaster]}`,

the last writer puts back its stale cart and the kettle is gone. Nothing
failed, so nothing was logged. `ReproduceProblem` replays exactly this
order.

## Solution
- **Write only what changed.** A request works on a `SessionView`; changes go
  through `set`/`delete`, which record the key. `SessionStore.commit` writes
  back only those keys, so the tracking call can no longer touch the cart.
- **Detect same-key conflicts.** Every key has a version number. The view
  remembers the versions it read, and `commit` refuses with
  `SessionConflict` if any changed key moved on in the meantime (two
  parallel "add to cart" calls). All keys are checked before any is
  written, so a refused commit changes nothing.
- **Retry on fresh data.** `run_with_retry` re-opens the session and
  re-runs the handler, so "add the toaster" is applied to the cart that
  already has the kettle, up to `DEFAULT_MAX_ATTEMPTS` (3) times.

The tests show that different keys both survive, read-only requests write
nothing, deletes are saved, same-key conflicts are refused and leave no
partial writes, and that a retry ends with both items in the cart.

## Trade-offs
- The real store must make "check versions, then write" atomic (a Redis
  `WATCH`/`MULTI` transaction or Lua script, a SQL `UPDATE ... WHERE
  version = ?`, DynamoDB conditional writes); this in-memory store relies on
  running in one thread.
- Handlers must call `set` after changing a value. Mutating a list returned
  by `get` changes only the request's copy and is not saved; the deep copies
  keep it from corrupting the store, but the change is lost.
- Better still, keep data that many requests change, such as the cart, out
  of the session: a `cart_items` table with one row per item makes "add" an
  insert that cannot conflict with anything.

## Run
```bash
python -m unittest -v test_solution.py
```
