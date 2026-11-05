# 037 · Logged-out visitors saw "Hello, <someone else's name>" on the home page

## Problem
A retail web API renders a personalised greeting and "recently viewed" panel
on its home page. It runs on a threaded server with a pool of 32 worker
threads per instance. After a refactor of the auth middleware, support got
screenshots from logged-out visitors whose home page said "Hello, Priya" and
listed Priya's recently viewed items. The audit log also showed anonymous
API calls attributed to real accounts. It looked random: it only happened
when an anonymous request landed on a worker thread that had just served a
logged-in user. It was treated as a privacy incident (one user's data shown
to another) and the personalised panel was switched off for two days.

## Root cause
The middleware kept the current user in a `threading.local` and had two
mistakes that only matter together:

1. **It only assigned the user when the request had a session.** For an
   anonymous request it "had nothing to set" and skipped the assignment.
2. **It never cleared the value at the end of the request.** A thread-local
   lives as long as the thread, and pooled threads live for the whole process.

So the next anonymous request on that thread read the previous user. In the
reproduction, a logged-in request followed by an anonymous one on the same
pooled thread returns `["Hello, alice", "Hello, alice"]`.

A thread-local is also the wrong tool under asyncio: all tasks on the event
loop share one thread. In the second reproduction two interleaved requests
(alice, then bob) both write the same slot, and alice's request reads `bob`
after its first `await`.

## Solution
`solution.py` keeps the original as `naive_middleware` and adds:

- **A `contextvars.ContextVar` for the current user.** Each thread has its
  own context, and each asyncio task gets a copy of the context when it is
  created, so tasks no longer share the value.
- **`request_scope(user)`**, a context manager that sets the user for every
  request (including `None` for anonymous ones) and calls `reset(token)` in a
  `finally`. The previous value comes back even if the handler raises, and
  nested scopes (for example a support "impersonate" action) restore the
  outer user.
- `safe_middleware` wraps each request in `request_scope`.

The tests serve requests on a one-thread pool so the thread is always reused,
check that 100 alternating logged-in/anonymous requests never leak, that a
handler exception still clears the user, and that interleaved asyncio tasks
each see their own user.

## Trade-offs
- **Explicit passing is clearer still.** Passing a `request` or `user` object
  into functions avoids hidden global state completely; a context variable is
  the compromise when deep library code (logging, ORM hooks) needs it.
- **Executors do not copy contexts for you.** Work handed to
  `ThreadPoolExecutor.submit` runs in the worker thread's context, not the
  caller's; use `contextvars.copy_context().run(...)` (or
  `asyncio.to_thread`, which copies it) when background work needs the user.
- **Set "unknown" explicitly.** Defaulting to `None` and always assigning is
  safer than "skip if nothing to set"; the same rule applies to tenant IDs,
  locale and request IDs stored this way.
- Frameworks often provide this already (request-scoped `g`, middleware
  contexts); the bug usually appears in hand-written middleware or in code
  that caches "the current user" in a module-level variable.

## Run
```bash
python -m unittest -v test_solution.py
```
