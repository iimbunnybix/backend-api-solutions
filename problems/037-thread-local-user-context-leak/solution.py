"""Stop one user's identity leaking into the next request on a pooled thread.

The original middleware stored the logged-in user in a ``threading.local``
and only set it when the request carried a session. Web servers reuse worker
threads, so an anonymous request that landed on a thread which had just served
Alice still saw "Alice" as the current user. Under asyncio it is worse: every
task on the event loop shares the one thread, so interleaved requests read
each other's user.

The fix stores the user in a ``contextvars.ContextVar``, sets it for EVERY
request (``None`` for anonymous) and always restores the previous value in a
``finally`` block, so nothing survives the end of a request.
"""
from __future__ import annotations

import contextvars
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Callable, Iterator, Optional

ANONYMOUS_GREETING = "Hello, guest"


@dataclass(frozen=True)
class Request:
    """A minimal HTTP request: just the user resolved from the session cookie."""

    path: str
    session_user: Optional[str] = None


Handler = Callable[[Request], str]


# --------------------------------------------------------------------------
# Original version: thread-local, set only when logged in, never cleared
# --------------------------------------------------------------------------
_thread_state = threading.local()


def naive_current_user() -> Optional[str]:
    """Read the user stored on this thread (whatever request set it last)."""
    return getattr(_thread_state, "user", None)


def naive_middleware(request: Request, handler: Handler) -> str:
    """Original middleware.

    It skips the assignment for anonymous requests ("nothing to set") and
    never clears the value afterwards, so a reused thread keeps the last user.
    """
    if request.session_user is not None:
        _thread_state.user = request.session_user
    return handler(request)


# --------------------------------------------------------------------------
# Fixed version: request-scoped ContextVar, always set, always reset
# --------------------------------------------------------------------------
_current_user: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "current_user", default=None)


def current_user() -> Optional[str]:
    """Return the user of the request that is running right now."""
    return _current_user.get()


@contextmanager
def request_scope(user: Optional[str]) -> Iterator[None]:
    """Make ``user`` the current user for exactly the duration of a request.

    ``reset(token)`` puts back whatever value was there before, even if the
    handler raises, so the next request on this thread (or task) starts clean.
    """
    token = _current_user.set(user)
    try:
        yield
    finally:
        _current_user.reset(token)


def safe_middleware(request: Request, handler: Handler) -> str:
    """Fixed middleware: anonymous requests explicitly get ``None``."""
    with request_scope(request.session_user):
        return handler(request)


# --------------------------------------------------------------------------
# Handlers shared by both versions (they read the user through a getter)
# --------------------------------------------------------------------------
def make_greeting_handler(get_user: Callable[[], Optional[str]]) -> Handler:
    """Build the home-page handler that personalises the greeting."""

    def greet(request: Request) -> str:
        user = get_user()
        if user is None:
            return ANONYMOUS_GREETING
        return f"Hello, {user}"

    return greet
