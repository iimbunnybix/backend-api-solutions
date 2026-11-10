# 045 · Anonymous visitors saw another company's unreleased products on the public catalogue page

## Problem
A multi-tenant B2B storefront API keeps every customer's products in one
Postgres table and isolates tenants with row-level security: the policy shows
public rows plus rows whose `tenant_id` equals the session variable
`app.tenant_id`. Signed-in handlers run `SET app.tenant_id = '<tenant>'` on
the connection they borrow from the application's connection pool. The
anonymous public catalogue sets nothing, because "no tenant" is supposed to
mean "public rows only".

A customer reported that their unreleased product appeared on the *public*
catalogue for a few seconds at a time. Separately, the DBA saw queries running
for 40 minutes on a database where every statement should time out after 30
seconds, and on-call saw "current transaction is aborted" errors on requests
that had not started a transaction. All three were intermittent and appeared
only under load.

In the simulation in `solution.py`, a public-catalogue request that reuses the
connection an `acme` dashboard request just returned sees
`acme-unreleased-phone`; after the monthly report, the next borrower's
statement timeout is `0` (unlimited); after a declined checkout, the next
borrower receives a connection still inside the failed transaction.

## Root cause
`SET` changes **session** state, and a pooled connection is one session that
lives for hours and serves thousands of requests. The pool returned
connections exactly as the last request left them, so:

- the tenant id from the previous request was still set (row-level security
  did exactly what it was told, for the wrong tenant);
- the report's `SET statement_timeout = 0` disabled the timeout for every
  later request on that connection;
- a handler that raised after `BEGIN` returned its connection with the
  transaction still open, so its locks were held and the next request's
  statements ran inside someone else's transaction.

It was intermittent because it depended on which idle connection a request
happened to get.

## Solution
1. `ConnectionPool.release` cleans each connection before putting it back:
   roll back any open transaction first (so settings changed inside it and its
   locks do not survive), then reset all session settings, the equivalent of
   Postgres `DISCARD ALL` / `RESET ALL`.
2. Handlers borrow connections through the `borrowed_connection` context
   manager, which always returns the connection in a `finally`, so the
   cleaning step also runs when the handler raises.

Connections are still reused (50 rounds of dashboard + public requests open
one connection in total), so the fix keeps the pool's benefit.

## Trade-offs
- Resetting costs one extra round trip per checkout/return. Most pools can do
  it for you: PgBouncer's `server_reset_query = DISCARD ALL` (session mode),
  SQLAlchemy's reset-on-return, HikariCP resets the settings it tracks.
- `SET LOCAL` (transaction-scoped) is safer still for the tenant id: it is
  undone automatically at commit or rollback. It requires every query to run
  inside a transaction, and in PgBouncer *transaction* pooling mode it is the
  only safe option, because session `SET` and `DISCARD ALL` do not work there.
- Do not let "no tenant set" mean "public": make the policy fail closed (no
  rows when the variable is missing) and use an explicit `'public'` value.
- Resetting does not protect against a request still using a connection after
  returning it; only the context manager discipline does.

## Run
```bash
python -m unittest -v test_solution.py
```
