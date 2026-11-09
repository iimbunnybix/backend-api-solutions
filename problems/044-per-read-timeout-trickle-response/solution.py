"""A read timeout is not a request timeout: a trickling response hangs workers.

The invoice service downloads tax data with ``timeout=10``. That timeout is
applied to each socket read, not to the whole response, so an overloaded
upstream that sends a small chunk every 9 seconds never trips it. One
"10-second" call can then hold a worker for hours.

The fix adds a total deadline for the whole download. Every read gets the
smaller of the per-read timeout and the time left before the deadline, and
the download is aborted with ``DeadlineExceeded`` once that time is used up.
"""
from __future__ import annotations

READ_TIMEOUT_SECONDS = 10.0
TOTAL_DEADLINE_SECONDS = 30.0
CHUNK_SIZE_BYTES = 1024


class DeadlineExceeded(TimeoutError):
    """The whole download took longer than its total deadline."""


class FakeClock:
    """A clock that only moves when the fake socket says time has passed."""

    def __init__(self) -> None:
        self.now = 0.0

    def advance(self, seconds: float) -> None:
        """Move time forward."""
        self.now += seconds


class FakeSlowSocket:
    """A socket that delivers pre-planned chunks after pre-planned delays.

    ``plan`` is a list of (seconds before the chunk arrives, chunk bytes).
    After the last chunk the server closes the connection (recv returns b"").
    """

    def __init__(self, clock: FakeClock, plan: list[tuple[float, bytes]]) -> None:
        self.clock = clock
        self.plan = list(plan)
        self.reads = 0

    def recv(self, max_bytes: int, timeout: float) -> bytes:
        """Wait up to ``timeout`` for the next chunk, like a socket with settimeout()."""
        self.reads += 1
        if not self.plan:
            return b""
        delay, chunk = self.plan[0]
        if delay > timeout:
            # Nothing arrived in time: the caller waited the full timeout.
            self.clock.advance(timeout)
            self.plan[0] = (delay - timeout, chunk)
            raise TimeoutError(f"no data for {timeout:.1f}s")
        self.clock.advance(delay)
        self.plan.pop(0)
        return chunk[:max_bytes]


def naive_download(sock: FakeSlowSocket,
                   read_timeout: float = READ_TIMEOUT_SECONDS) -> bytes:
    """Original client: the timeout is only checked per read.

    This is what ``requests.get(url, timeout=10)`` does: a server that sends
    one byte every 9 seconds is never timed out.
    """
    body = bytearray()
    while True:
        chunk = sock.recv(CHUNK_SIZE_BYTES, timeout=read_timeout)
        if not chunk:
            return bytes(body)
        body.extend(chunk)


def download_with_deadline(sock: FakeSlowSocket, clock: FakeClock,
                           total_deadline: float = TOTAL_DEADLINE_SECONDS,
                           read_timeout: float = READ_TIMEOUT_SECONDS) -> bytes:
    """Fixed client: per-read timeout *and* a deadline for the whole body."""
    if total_deadline <= 0 or read_timeout <= 0:
        raise ValueError("timeouts must be positive")
    deadline = clock.now + total_deadline
    body = bytearray()
    while True:
        remaining = deadline - clock.now
        if remaining <= 0:
            raise DeadlineExceeded(
                f"response not complete after {total_deadline:.1f}s "
                f"({len(body)} bytes received)")
        # Never wait past the deadline, even if the per-read timeout allows it.
        this_read_timeout = min(read_timeout, remaining)
        try:
            chunk = sock.recv(CHUNK_SIZE_BYTES, timeout=this_read_timeout)
        except TimeoutError:
            if clock.now >= deadline:
                raise DeadlineExceeded(
                    f"response not complete after {total_deadline:.1f}s "
                    f"({len(body)} bytes received)") from None
            raise
        if not chunk:
            return bytes(body)
        body.extend(chunk)


def trickle_plan(chunk_count: int, seconds_between_chunks: float,
                 chunk: bytes = b"x" * 100) -> list[tuple[float, bytes]]:
    """A response that arrives as ``chunk_count`` small chunks, evenly spaced."""
    plan = []
    for _ in range(chunk_count):
        plan.append((seconds_between_chunks, chunk))
    return plan
