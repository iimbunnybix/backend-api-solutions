"""Connection pool that hands out dead connections after a database restart.

A naive pool keeps idle connections in a list and hands the next one out
without checking it. When the database restarts or fails over, every idle
connection is already dead (or still attached to the old, now read-only
primary), so the next requests each pick one up and fail, and because the
request code returns the connection afterwards, the dead ones stay in the pool.

The fix validates on checkout: a connection idle for longer than a threshold
gets a cheap ``SELECT 1`` ping first. A connection that fails a ping or a
query is discarded, and one disconnect error marks every older connection as
suspect. A maximum lifetime retires connections even when they look healthy,
so after a failover the pool reconnects to the new primary within a bounded
time.
"""
from __future__ import annotations

from typing import Callable

# Only connections idle longer than this get a ping: a connection used a
# moment ago almost certainly still works, and pinging every checkout would
# add a round trip to every request.
IDLE_PING_THRESHOLD_SECONDS = 30.0
# Retire connections after this long, even healthy-looking ones.
MAX_LIFETIME_SECONDS = 30 * 60.0
PING_SQL = "SELECT 1"


class ConnectionBroken(Exception):
    """The server side of the connection is gone (restart, failover, reset)."""


class ReadOnlyServer(Exception):
    """A write reached a server that is no longer the primary."""


class QueryError(Exception):
    """The SQL itself was rejected; the connection is still fine."""


KNOWN_STATEMENTS = ("SELECT", "INSERT", "UPDATE", "DELETE")
# Errors after which a connection must never be reused.
DISCARD_ERRORS = (ConnectionBroken, ReadOnlyServer)


class FakeServer:
    """One database server. ``boot_id`` changes on every restart."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.boot_id = 1
        self.read_only = False

    def restart(self) -> None:
        """Every existing connection to this server dies."""
        self.boot_id += 1


class FakeCluster:
    """A primary plus a replica; ``connect`` goes to whichever is primary."""

    def __init__(self) -> None:
        self.primary = FakeServer("db-a")
        self.replica = FakeServer("db-b")
        self.replica.read_only = True
        self.connections_opened = 0

    def connect(self) -> "FakeConnection":
        """Open a new connection to the current primary (like a DNS lookup)."""
        self.connections_opened += 1
        return FakeConnection(self.primary)

    def fail_over(self) -> None:
        """Promote the replica; the old primary stays up but read-only."""
        old_primary = self.primary
        self.primary = self.replica
        self.primary.read_only = False
        old_primary.read_only = True
        self.replica = old_primary


class FakeConnection:
    """A client connection bound to one server boot."""

    def __init__(self, server: FakeServer) -> None:
        self.server = server
        self.boot_id = server.boot_id
        self.closed = False
        self.created_at = 0.0
        self.last_used_at = 0.0
        self.pool_epoch = 0

    def execute(self, sql: str) -> str:
        """Run ``sql``; fail if the server restarted or rejects writes."""
        if self.closed or self.server.boot_id != self.boot_id:
            raise ConnectionBroken("server closed the connection unexpectedly")
        if sql.startswith(("INSERT", "UPDATE", "DELETE")) and self.server.read_only:
            raise ReadOnlyServer("cannot execute in a read-only transaction")
        if not sql.startswith(KNOWN_STATEMENTS):
            raise QueryError(f"syntax error in {sql!r}")
        return f"ok from {self.server.name}"

    def close(self) -> None:
        """Close the client side of the connection."""
        self.closed = True


class NaivePool:
    """The original pool: reuses whatever idle connection is on top."""

    def __init__(self, cluster: FakeCluster) -> None:
        self.cluster = cluster
        self.idle: list[FakeConnection] = []

    def acquire(self) -> FakeConnection:
        """Hand out an idle connection without checking it."""
        if self.idle:
            return self.idle.pop()
        return self.cluster.connect()

    def release(self, conn: FakeConnection) -> None:
        """Put the connection back, whatever happened to it."""
        self.idle.append(conn)


def naive_run_query(pool: NaivePool, sql: str) -> str:
    """The original request code: always returns the connection to the pool."""
    conn = pool.acquire()
    try:
        return conn.execute(sql)
    finally:
        pool.release(conn)


class ValidatingPool:
    """Pool with validate-on-checkout, discard-on-error and max lifetime."""

    def __init__(self, cluster: FakeCluster, clock: Callable[[], float],
                 idle_ping_threshold: float = IDLE_PING_THRESHOLD_SECONDS,
                 max_lifetime: float = MAX_LIFETIME_SECONDS) -> None:
        self.cluster = cluster
        self.clock = clock
        self.idle_ping_threshold = idle_ping_threshold
        self.max_lifetime = max_lifetime
        self.idle: list[FakeConnection] = []
        self.pings_sent = 0
        self.discarded = 0
        # Bumped on every disconnect error; connections opened in an earlier
        # epoch are suspect.
        self.epoch = 0

    def acquire(self) -> FakeConnection:
        """Return a connection that is young enough and known to work."""
        while self.idle:
            conn = self.idle.pop()
            if self._is_usable(conn):
                return conn
            self._discard(conn)
        return self._open()

    def release(self, conn: FakeConnection, broken: bool = False) -> None:
        """Return a connection; a broken one also invalidates its peers."""
        if broken:
            # One disconnect usually means the server restarted, so every
            # connection opened before now is likely dead too.
            self.epoch += 1
            self._discard(conn)
            return
        if self._is_expired(conn):
            self._discard(conn)
            return
        conn.last_used_at = self.clock()
        self.idle.append(conn)

    def _is_usable(self, conn: FakeConnection) -> bool:
        """Lifetime, invalidation and (only if idle long) a ping."""
        if self._is_expired(conn) or self._is_invalidated(conn):
            return False
        idle_for = self.clock() - conn.last_used_at
        if idle_for <= self.idle_ping_threshold:
            return True
        return self._ping(conn)

    def _is_expired(self, conn: FakeConnection) -> bool:
        """True once the connection is older than the maximum lifetime."""
        return self.clock() - conn.created_at >= self.max_lifetime

    def _is_invalidated(self, conn: FakeConnection) -> bool:
        """True if it was opened before the last disconnect error."""
        return conn.pool_epoch < self.epoch

    def _ping(self, conn: FakeConnection) -> bool:
        """Cheap round trip; any error means the connection is dead."""
        self.pings_sent += 1
        try:
            conn.execute(PING_SQL)
        except DISCARD_ERRORS:
            return False
        return True

    def _open(self) -> FakeConnection:
        """Open a fresh connection and stamp its creation time."""
        conn = self.cluster.connect()
        conn.created_at = self.clock()
        conn.last_used_at = conn.created_at
        conn.pool_epoch = self.epoch
        return conn

    def _discard(self, conn: FakeConnection) -> None:
        """Close a connection and never hand it out again."""
        conn.close()
        self.discarded += 1


def run_query(pool: ValidatingPool, sql: str) -> str:
    """Run ``sql``, discarding the connection if the error says it is dead."""
    conn = pool.acquire()
    broken = False
    try:
        return conn.execute(sql)
    except DISCARD_ERRORS:
        broken = True
        raise
    finally:
        # Other errors (bad SQL, constraint violations) leave the connection
        # fine, so it still goes back to the pool.
        pool.release(conn, broken=broken)
