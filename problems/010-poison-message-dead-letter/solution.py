"""Stop one malformed queue message from blocking every message behind it.

``InMemoryQueue`` mimics the semantics of common brokers: a received message is
invisible until it is acked; a ``nack`` makes it visible again (optionally after
a delay) and the broker counts deliveries.

``naive_consume`` is the original consumer: on any exception it nacks with
immediate requeue, so a message that can never succeed is redelivered forever
at the head of the queue.

``ResilientConsumer`` is the fix: classify errors, retry transient ones with
exponential backoff up to ``max_attempts``, and move permanent failures (or
exhausted retries) to a dead-letter queue with the reason attached.
"""
from __future__ import annotations

import json
from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Deque, Optional


class FakeClock:
    def __init__(self, start: float = 0.0) -> None:
        self.t = start

    def now(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


class PermanentError(Exception):
    """The message itself is bad; retrying can never succeed."""


class TransientError(Exception):
    """A dependency hiccup; retrying later may succeed."""


@dataclass
class Message:
    id: str
    body: str
    deliveries: int = 0
    visible_at: float = 0.0


@dataclass
class DeadLetter:
    message: Message
    reason: str
    error_type: str


class InMemoryQueue:
    """FIFO queue with visibility delays and delivery counts (single consumer)."""

    def __init__(self, clock: FakeClock) -> None:
        self.clock = clock
        self._items: Deque[Message] = deque()
        self._in_flight: dict[str, Message] = {}

    def publish(self, msg_id: str, body: str) -> None:
        self._items.append(Message(msg_id, body))

    def receive(self) -> Optional[Message]:
        """Return the first visible message, or None."""
        now = self.clock.now()
        for i, msg in enumerate(self._items):
            if msg.visible_at <= now:
                del self._items[i]
                msg.deliveries += 1
                self._in_flight[msg.id] = msg
                return msg
        return None

    def ack(self, msg: Message) -> None:
        self._in_flight.pop(msg.id, None)

    def nack(self, msg: Message, delay: float = 0.0) -> None:
        """Return the message to the queue. Immediate requeue puts it back at the head."""
        self._in_flight.pop(msg.id, None)
        msg.visible_at = self.clock.now() + delay
        if delay == 0:
            self._items.appendleft(msg)
        else:
            self._items.append(msg)

    def next_visible_at(self) -> Optional[float]:
        return min((m.visible_at for m in self._items), default=None)

    def __len__(self) -> int:
        return len(self._items) + len(self._in_flight)


# --------------------------------------------------------------- the handler
def handle_order_event(body: str, processed: list) -> None:
    """Business handler: parse an order event and record it."""
    try:
        event = json.loads(body)
    except json.JSONDecodeError as exc:
        raise PermanentError(f"invalid JSON: {exc.msg}") from exc
    if not isinstance(event, dict) or "order_id" not in event:
        raise PermanentError("missing order_id")
    processed.append(event["order_id"])


def naive_consume(queue: InMemoryQueue, handler: Callable[[str], None], max_receives: int) -> int:
    """Original consumer: requeue immediately on any error. Returns messages acked."""
    acked = 0
    for _ in range(max_receives):
        msg = queue.receive()
        if msg is None:
            break
        try:
            handler(msg.body)
        except Exception:
            queue.nack(msg)          # back to the head of the queue, forever
            continue
        queue.ack(msg)
        acked += 1
    return acked


@dataclass
class ResilientConsumer:
    """Consumer with bounded retries, backoff and a dead-letter queue.

    Args:
        max_attempts: total deliveries before a transiently failing message is
            dead-lettered.
        base_delay / max_delay: exponential backoff for transient retries
            (base * 2**(attempt-1), capped).
        dead_letters: where poison messages go, with the failure reason.
    """

    queue: InMemoryQueue
    handler: Callable[[str], None]
    max_attempts: int = 5
    base_delay: float = 1.0
    max_delay: float = 60.0
    dead_letters: list = field(default_factory=list)
    acked: int = 0

    def backoff(self, attempt: int) -> float:
        """Delay before retry ``attempt``: base * 2**(attempt-1), capped at max_delay."""
        # Double step by step and stop at the cap. Computing 2 ** (attempt - 1)
        # first overflows float conversion from attempt 1,025 on.
        delay = self.base_delay
        for _ in range(attempt - 1):
            if delay >= self.max_delay:
                break
            delay *= 2
        return min(self.max_delay, delay)

    def process_one(self) -> bool:
        """Receive and handle one message. Returns False when nothing is visible."""
        msg = self.queue.receive()
        if msg is None:
            return False
        try:
            self.handler(msg.body)
        except PermanentError as exc:
            self._dead_letter(msg, exc)
        except Exception as exc:  # unknown errors are treated as transient but bounded
            if msg.deliveries >= self.max_attempts:
                self._dead_letter(msg, exc)
            else:
                self.queue.nack(msg, delay=self.backoff(msg.deliveries))
        else:
            self.queue.ack(msg)
            self.acked += 1
        return True

    def _dead_letter(self, msg: Message, exc: Exception) -> None:
        self.dead_letters.append(DeadLetter(msg, str(exc), type(exc).__name__))
        self.queue.ack(msg)  # remove from the main queue only after it is safely stored

    def drain(self, clock: FakeClock, max_steps: int = 100_000) -> None:
        """Process until the queue is empty, advancing the fake clock over backoff gaps."""
        for _ in range(max_steps):
            if self.process_one():
                continue
            nxt = self.queue.next_visible_at()
            if nxt is None:
                return
            clock.advance(max(0.0, nxt - clock.now()))
        raise RuntimeError("drain did not finish")


def replay_dead_letters(dead_letters: list, queue: InMemoryQueue,
                        fix: Callable[[str], Optional[str]]) -> int:
    """Republish dead letters after a fix (e.g. a patched body). Returns count replayed.

    ``fix`` returns the corrected body, or None to leave the letter parked.
    """
    replayed = 0
    # Iterate over a copy and remove each letter right after publishing it:
    # if ``fix`` raises halfway, the DLQ still holds exactly the letters that
    # were NOT republished, so a re-run cannot publish any letter twice.
    for letter in list(dead_letters):
        body = fix(letter.message.body)
        if body is None:
            continue
        queue.publish(letter.message.id, body)
        dead_letters.remove(letter)
        replayed += 1
    return replayed
