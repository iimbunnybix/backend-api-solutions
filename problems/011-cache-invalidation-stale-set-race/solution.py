"""Stop a slow cache-aside read from writing a stale value back after an update.

Cache-aside ("read from cache; on a miss read the DB and populate the cache;
on a write update the DB and delete the cache key") has a well-known race:

    reader: cache miss -> reads DB (old value v1) ......... cache.set(v1)
    writer:                 updates DB to v2 -> cache.delete()

If the reader's ``set`` lands *after* the writer's ``delete``, the cache holds
the old value until the TTL expires, and nothing will ever correct it sooner.

``NaiveMenuService`` reproduces this. ``VersionedMenuService`` fixes it:

* every DB row carries a monotonically increasing ``version``;
* the cache only accepts ``set_if_newer`` (an atomic compare on version, the
  same thing a small Redis Lua script or memcached CAS would do);
* a write does not just delete the key: it leaves a short-lived *tombstone*
  holding the new version, so a slow reader that read the old row cannot
  re-populate the key with it.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Callable, Dict, Optional, Tuple


class FakeClock:
    def __init__(self, start: float = 0.0) -> None:
        self.t = start

    def now(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


class Database:
    """Rows are ``key -> (value, version)``; each write bumps the version."""

    def __init__(self) -> None:
        self._rows: Dict[str, Tuple[object, int]] = {}
        self._lock = threading.Lock()
        self.reads = 0

    def read(self, key: str) -> Tuple[object, int]:
        with self._lock:
            self.reads += 1
            return self._rows[key]

    def write(self, key: str, value: object) -> int:
        with self._lock:
            _, version = self._rows.get(key, (None, 0))
            self._rows[key] = (value, version + 1)
            return version + 1


@dataclass
class Entry:
    value: object
    version: int
    expires_at: float
    tombstone: bool = False


class Cache:
    """A tiny Redis-like cache with TTLs and an atomic versioned set."""

    def __init__(self, clock: FakeClock) -> None:
        self.clock = clock
        self._data: Dict[str, Entry] = {}
        self._lock = threading.Lock()

    def _live(self, key: str) -> Optional[Entry]:
        e = self._data.get(key)
        if e is not None and e.expires_at <= self.clock.now():
            del self._data[key]
            return None
        return e

    def get(self, key: str) -> Optional[Entry]:
        with self._lock:
            return self._live(key)

    # --- naive API -------------------------------------------------------
    def set(self, key: str, value: object, ttl: float, version: int = 0) -> None:
        with self._lock:
            self._data[key] = Entry(value, version, self.clock.now() + ttl)

    def delete(self, key: str) -> None:
        with self._lock:
            self._data.pop(key, None)

    # --- fixed API (each method is one atomic operation) -------------------
    def set_if_newer(self, key: str, value: object, version: int, ttl: float) -> bool:
        """Store ``value`` only if it is not older than what the cache knows.

        A live value is replaced only by a strictly higher version. A tombstone
        written by an update at version N accepts version N or higher: N is
        exactly the row the writer just committed.
        """
        with self._lock:
            cur = self._live(key)
            if cur is not None:
                if cur.tombstone and version < cur.version:
                    return False
                if not cur.tombstone and version <= cur.version:
                    return False
            self._data[key] = Entry(value, version, self.clock.now() + ttl)
            return True

    def invalidate(self, key: str, version: int, tombstone_ttl: float) -> None:
        """Replace the key with a tombstone for ``version`` (never lowers it)."""
        with self._lock:
            cur = self._live(key)
            if cur is not None and cur.version > version:
                return  # a newer write already invalidated or refilled it
            self._data[key] = Entry(None, version, self.clock.now() + tombstone_ttl,
                                    tombstone=True)


Hook = Optional[Callable[[], None]]


class NaiveMenuService:
    """Original cache-aside code: plain ``set`` on miss, ``delete`` on write."""

    def __init__(self, db: Database, cache: Cache, ttl: float = 86_400) -> None:
        self.db, self.cache, self.ttl = db, cache, ttl

    def get_item(self, key: str, after_db_read: Hook = None) -> object:
        e = self.cache.get(key)
        if e is not None:
            return e.value
        value, _ = self.db.read(key)
        if after_db_read:          # e.g. a GC pause or a slow network hop
            after_db_read()
        self.cache.set(key, value, self.ttl)
        return value

    def update_item(self, key: str, value: object) -> None:
        self.db.write(key, value)
        self.cache.delete(key)


class VersionedMenuService:
    """Cache-aside with versioned sets and tombstones on write."""

    def __init__(self, db: Database, cache: Cache, ttl: float = 86_400,
                 tombstone_ttl: float = 60) -> None:
        # tombstone_ttl must exceed the slowest possible read (DB read + set),
        # otherwise a reader slower than that can still write a stale value.
        self.db, self.cache = db, cache
        self.ttl, self.tombstone_ttl = ttl, tombstone_ttl

    def get_item(self, key: str, after_db_read: Hook = None) -> object:
        e = self.cache.get(key)
        if e is not None and not e.tombstone:
            return e.value
        value, version = self.db.read(key)
        if after_db_read:
            after_db_read()
        self.cache.set_if_newer(key, value, version, self.ttl)
        return value

    def update_item(self, key: str, value: object) -> None:
        version = self.db.write(key, value)
        self.cache.invalidate(key, version, self.tombstone_ttl)
