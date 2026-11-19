"""Stop parallel requests from wiping each other's session changes.

The web shop kept each user's session (cart, recently viewed items, locale)
as one blob. Every request loaded the whole blob, changed something, and
saved the whole blob back at the end. When the product page's "recently
viewed" call and the "add to cart" call ran at the same time, whichever
saved last wrote back its stale copy of the cart, and the added item vanished.

The fix records which keys a request actually changed, writes back only
those keys, and gives every key a version number. If another request changed
the same key in the meantime, the commit is refused and the request is
retried on fresh data instead of silently overwriting it.
"""
from __future__ import annotations

import copy
from typing import Callable

DEFAULT_MAX_ATTEMPTS = 3


class SessionConflict(Exception):
    """Another request changed a key this request also changed."""


class SessionView:
    """One request's private copy of a session, remembering what it changed."""

    def __init__(self, session_id: str, values: dict[str, object], versions: dict[str, int]) -> None:
        self.session_id = session_id
        self._values = values
        self.versions_read = versions
        self.changed_keys: set[str] = set()

    def get(self, key: str, default: object = None) -> object:
        """Read a value from this request's copy."""
        return self._values.get(key, default)

    def set(self, key: str, value: object) -> None:
        """Change a value; only keys changed through set/delete are saved."""
        self._values[key] = value
        self.changed_keys.add(key)

    def delete(self, key: str) -> None:
        """Remove a key; the removal is saved like any other change."""
        self._values.pop(key, None)
        self.changed_keys.add(key)

    def has(self, key: str) -> bool:
        """True if the key exists in this request's copy."""
        return key in self._values


class SessionStore:
    """In-memory session store with a version number per session key."""

    def __init__(self) -> None:
        self._values: dict[str, dict[str, object]] = {}
        self._versions: dict[str, dict[str, int]] = {}

    def snapshot(self, session_id: str) -> dict[str, object]:
        """A deep copy of a session, so callers cannot mutate stored data."""
        return copy.deepcopy(self._values.get(session_id, {}))

    def naive_save(self, session_id: str, values: dict[str, object]) -> None:
        """The original save: replace the whole session with this copy."""
        self._values[session_id] = copy.deepcopy(values)

    def open(self, session_id: str) -> SessionView:
        """Start a request: private copy of the values plus the versions seen."""
        versions = dict(self._versions.get(session_id, {}))
        return SessionView(session_id, self.snapshot(session_id), versions)

    def commit(self, view: SessionView) -> None:
        """Save only the keys this request changed, if nobody else changed them.

        All keys are checked before any is written, so a refused commit
        leaves the session exactly as it was.
        """
        stored_versions = self._versions.setdefault(view.session_id, {})
        for key in view.changed_keys:
            if stored_versions.get(key, 0) != view.versions_read.get(key, 0):
                raise SessionConflict(f"session key {key!r} was changed by another request")
        stored_values = self._values.setdefault(view.session_id, {})
        for key in view.changed_keys:
            self._write_key(stored_values, view, key)
            stored_versions[key] = stored_versions.get(key, 0) + 1

    @staticmethod
    def _write_key(stored_values: dict[str, object], view: SessionView, key: str) -> None:
        """Copy one changed key (or its deletion) from the view into storage."""
        if view.has(key):
            stored_values[key] = copy.deepcopy(view.get(key))
        else:
            stored_values.pop(key, None)


RequestHandler = Callable[[SessionView], None]


def run_with_retry(store: SessionStore, session_id: str, handler: RequestHandler,
                   max_attempts: int = DEFAULT_MAX_ATTEMPTS) -> None:
    """Run a request's session logic, re-running it on fresh data after a conflict.

    Re-running the handler (instead of re-applying the old result) matters:
    "add kettle to cart" must be applied to the cart as it is *now*.
    """
    for attempt in range(1, max_attempts + 1):
        view = store.open(session_id)
        handler(view)
        try:
            store.commit(view)
            return
        except SessionConflict:
            if attempt == max_attempts:
                raise
