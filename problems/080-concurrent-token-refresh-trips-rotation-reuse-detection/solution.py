"""Refresh a rotating OAuth token once, not once per concurrent request.

Our CRM sync calls a partner API from a pool of worker threads. The partner
rotates refresh tokens: each refresh returns a new refresh token and retires
the old one, and presenting a retired one is treated as theft, so the whole
token family is revoked. When the access token expired, every busy thread
noticed at the same moment and refreshed with the same refresh token. One
succeeded, the rest presented a retired token, and the partner revoked
everything, disconnecting the customer's integration.

Fix: a single lock around "check expiry, refresh if needed", with a re-check
after the lock is taken, so exactly one thread refreshes and the others reuse
its result. Tokens are also refreshed a little before they expire.
"""
from __future__ import annotations

import itertools
import threading
import time
from dataclasses import dataclass

ACCESS_TOKEN_LIFETIME_SECONDS = 3600
REFRESH_EARLY_SECONDS = 60          # refresh before expiry so in-flight calls do not 401
REFRESH_NETWORK_SECONDS = 0.05      # modelled latency of the token endpoint


class FakeClock:
    """Wall clock the tests move by hand, so expiry is deterministic."""

    def __init__(self) -> None:
        self.now = 0.0

    def advance(self, seconds: float) -> None:
        self.now += seconds


class InvalidGrant(Exception):
    """The authorization server rejected the refresh token."""


@dataclass(frozen=True)
class TokenPair:
    access_token: str
    refresh_token: str
    expires_at: float


class RotatingAuthServer:
    """Token endpoint with refresh-token rotation and reuse detection."""

    def __init__(self, clock: FakeClock) -> None:
        self.clock = clock
        self._ids = itertools.count(1)
        self._lock = threading.Lock()
        self._current_refresh: str | None = None
        self._retired: set[str] = set()
        self.revoked = False
        self.refresh_calls = 0

    def issue_initial(self) -> TokenPair:
        """The pair the customer got when they connected the integration."""
        with self._lock:
            return self._new_pair()

    def refresh(self, refresh_token: str) -> TokenPair:
        """Exchange a refresh token for a new pair, retiring the old one."""
        time.sleep(REFRESH_NETWORK_SECONDS)
        with self._lock:
            self.refresh_calls += 1
            if refresh_token in self._retired:
                # WHY: a retired token being used again means two parties hold
                # it, so the server assumes theft and revokes the whole family.
                self.revoked = True
            if self.revoked or refresh_token != self._current_refresh:
                raise InvalidGrant("refresh token is not valid")
            self._retired.add(refresh_token)
            return self._new_pair()

    def accepts(self, access_token: str) -> bool:
        """Would the partner API accept a call with this access token?"""
        return not self.revoked and access_token.startswith("access-")

    def _new_pair(self) -> TokenPair:
        number = next(self._ids)
        pair = TokenPair(f"access-{number}", f"refresh-{number}",
                         self.clock.now + ACCESS_TOKEN_LIFETIME_SECONDS)
        self._current_refresh = pair.refresh_token
        return pair


class OriginalTokenClient:
    """Original client: every thread that sees an expired token refreshes it."""

    def __init__(self, server: RotatingAuthServer, clock: FakeClock) -> None:
        self.server = server
        self.clock = clock
        self.pair = server.issue_initial()

    def get_access_token(self) -> str:
        if self.clock.now >= self.pair.expires_at:
            self.pair = self.server.refresh(self.pair.refresh_token)
        return self.pair.access_token


class TokenClient:
    """Fixed client: at most one refresh in flight; other threads wait and reuse it."""

    def __init__(self, server: RotatingAuthServer, clock: FakeClock) -> None:
        self.server = server
        self.clock = clock
        self.pair = server.issue_initial()
        self._refresh_lock = threading.Lock()

    def get_access_token(self) -> str:
        """Return a valid access token, refreshing it at most once per expiry."""
        if not self._needs_refresh():
            return self.pair.access_token
        with self._refresh_lock:
            # WHY: re-check after waiting; another thread may have refreshed
            # while we were blocked, and refreshing again would reuse a
            # retired refresh token.
            if self._needs_refresh():
                self.pair = self.server.refresh(self.pair.refresh_token)
            return self.pair.access_token

    def _needs_refresh(self) -> bool:
        """True when the token expires within REFRESH_EARLY_SECONDS."""
        return self.clock.now >= self.pair.expires_at - REFRESH_EARLY_SECONDS


def call_from_many_threads(client: OriginalTokenClient | TokenClient,
                           thread_count: int) -> tuple[list[str], int]:
    """Have ``thread_count`` threads ask for a token at the same moment.

    Returns the tokens they got and how many failed with InvalidGrant.
    """
    start_together = threading.Barrier(thread_count)
    tokens: list[str] = []
    failures = 0
    results_lock = threading.Lock()

    def worker() -> None:
        nonlocal failures
        start_together.wait()
        try:
            token = client.get_access_token()
        except InvalidGrant:
            with results_lock:
                failures += 1
            return
        with results_lock:
            tokens.append(token)

    threads = [threading.Thread(target=worker) for _ in range(thread_count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return tokens, failures
