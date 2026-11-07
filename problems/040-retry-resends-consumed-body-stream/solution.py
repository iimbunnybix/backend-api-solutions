"""Retries that resend an empty request body because the stream was used up.

An upload client wraps POSTs in a retry loop for connection resets. The body
is a generator (or an open file) that streams the document. The first attempt
reads it to the end; when the connection resets and the loop tries again, the
same, now exhausted, stream yields nothing, and the server stores a 0-byte
document and answers 201.

The fix turns every body into a ``ReplayableBody`` that can produce the full
bytes again for each attempt: bytes are reused, seekable files are rewound to
where they started, and one-shot iterators are buffered up to a size limit.
A body that cannot be replayed is sent once and never retried.
"""
from __future__ import annotations

import io
from typing import Iterable, Iterator, Protocol

MAX_ATTEMPTS = 3
# Larger one-shot streams are not buffered in memory just to make them
# retryable; they get a single attempt instead.
MAX_BUFFER_BYTES = 8 * 1024 * 1024
CHUNK_BYTES = 64 * 1024


class Transport(Protocol):
    def send(self, url: str, chunks: Iterable[bytes]) -> int:
        """Send a POST body and return the HTTP status code."""


def naive_post_with_retry(transport: Transport, url: str, body: Iterable[bytes],
                          max_attempts: int = MAX_ATTEMPTS) -> int:
    """The original code: hands the same stream to every attempt."""
    for attempt in range(1, max_attempts + 1):
        try:
            return transport.send(url, body)
        except ConnectionResetError:
            if attempt == max_attempts:
                raise
    raise AssertionError("unreachable")


class ReplayableBody:
    """A request body that yields the same bytes on every ``chunks()`` call."""

    def __init__(self, replay_chunks: list[bytes] | None = None,
                 seekable_file: io.IOBase | None = None, start_offset: int = 0,
                 one_shot: Iterator[bytes] | None = None) -> None:
        self._replay_chunks = replay_chunks
        self._file = seekable_file
        self._start_offset = start_offset
        self._one_shot = one_shot
        self._one_shot_used = False

    @property
    def can_replay(self) -> bool:
        """False for a large stream that we chose not to buffer."""
        return self._one_shot is None

    def chunks(self) -> Iterator[bytes]:
        """A fresh iterator over the complete body."""
        if self._replay_chunks is not None:
            return iter(self._replay_chunks)
        if self._file is not None:
            # Rewind to where the caller's file was, not to 0: the caller may
            # have written a header first and positioned the file after it.
            self._file.seek(self._start_offset)
            return _read_in_chunks(self._file)
        if self._one_shot_used:
            raise RuntimeError("one-shot body was already sent")
        self._one_shot_used = True
        return self._one_shot


def _read_in_chunks(file: io.IOBase) -> Iterator[bytes]:
    """Read a file from its current position to the end, chunk by chunk."""
    while True:
        chunk = file.read(CHUNK_BYTES)
        if not chunk:
            return
        yield chunk


def _is_seekable_file(body: object) -> bool:
    """A file we can rewind: has read() and reports itself as seekable."""
    if not hasattr(body, "read") or not hasattr(body, "seekable"):
        return False
    return bool(body.seekable())


def _buffer_stream(stream: Iterator[bytes], limit: int) -> ReplayableBody:
    """Buffer a one-shot stream if it fits in ``limit`` bytes."""
    buffered: list[bytes] = []
    size = 0
    for chunk in stream:
        buffered.append(chunk)
        size += len(chunk)
        if size > limit:
            # Too big to hold: send what we read followed by the rest, once.
            return ReplayableBody(one_shot=_chain(buffered, stream))
    return ReplayableBody(replay_chunks=buffered)


def _chain(head: list[bytes], tail: Iterator[bytes]) -> Iterator[bytes]:
    """The chunks already read, then the remainder of the stream."""
    yield from head
    yield from tail


def make_replayable(body: bytes | io.IOBase | Iterable[bytes],
                    limit: int = MAX_BUFFER_BYTES) -> ReplayableBody:
    """Wrap any supported body type so each attempt sends all of it."""
    if isinstance(body, (bytes, bytearray)):
        return ReplayableBody(replay_chunks=[bytes(body)])
    if _is_seekable_file(body):
        return ReplayableBody(seekable_file=body, start_offset=body.tell())
    return _buffer_stream(iter(body), limit)


def post_with_retry(transport: Transport, url: str,
                    body: bytes | io.IOBase | Iterable[bytes],
                    max_attempts: int = MAX_ATTEMPTS,
                    buffer_limit: int = MAX_BUFFER_BYTES) -> int:
    """The fix: every attempt sends the complete body, or there is no retry."""
    replayable = make_replayable(body, buffer_limit)
    attempts_allowed = max_attempts if replayable.can_replay else 1
    for attempt in range(1, attempts_allowed + 1):
        try:
            return transport.send(url, replayable.chunks())
        except ConnectionResetError:
            if attempt == attempts_allowed:
                raise
    raise AssertionError("unreachable")
