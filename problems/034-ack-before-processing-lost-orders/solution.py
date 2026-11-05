"""Queue consumer that does not lose orders when the worker is killed mid-batch.

The fulfilment worker received a batch of order messages and acknowledged
the whole batch straight away, then saved the orders one by one. When a
deploy or the OOM killer stopped the process mid-batch, the broker had
already forgotten those messages, so every order still in the batch was
lost without an error anywhere.

The fix acknowledges a message only after its order is committed. If the
process dies first, the broker redelivers the message to the next worker.
Because a crash between commit and ack also causes a redelivery, the write
is made idempotent (insert-if-absent on order_id), so redelivery never
creates a second order.
"""
from __future__ import annotations

import json
import sqlite3
from collections import deque
from dataclasses import dataclass, field
from typing import Callable

BATCH_SIZE = 50


class ProcessKilled(BaseException):
    """The worker process died (SIGKILL on deploy, OOM kill). Nothing after it runs."""


@dataclass
class Delivery:
    tag: int
    body: str
    redelivered: bool = False


class InMemoryBroker:
    """Queue with acks: unacked messages come back when the consumer's connection drops."""

    def __init__(self) -> None:
        self._ready: deque[Delivery] = deque()
        self._unacked: dict[int, Delivery] = {}
        self._next_tag = 1
        self.redeliveries = 0

    def publish(self, body: str) -> None:
        """Add a message to the end of the queue."""
        self._ready.append(Delivery(self._next_tag, body))
        self._next_tag += 1

    def receive(self, max_messages: int, ack_on_receive: bool) -> list[Delivery]:
        """Hand out up to ``max_messages``.

        With ``ack_on_receive`` the broker deletes them immediately (the bug):
        from then on only the worker's memory holds them.
        """
        batch: list[Delivery] = []
        while self._ready and len(batch) < max_messages:
            delivery = self._ready.popleft()
            if not ack_on_receive:
                self._unacked[delivery.tag] = delivery
            batch.append(delivery)
        return batch

    def ack(self, tag: int) -> None:
        """Delete a delivered message for good."""
        self._unacked.pop(tag, None)

    def connection_lost(self) -> None:
        """Requeue every unacked message at the front, as brokers do when a consumer dies."""
        # Sort so redelivery keeps the original order.
        for tag in sorted(self._unacked, reverse=True):
            delivery = self._unacked.pop(tag)
            delivery.redelivered = True
            self._ready.appendleft(delivery)
            self.redeliveries += 1

    def is_drained(self) -> bool:
        """True when nothing is waiting and nothing is awaiting an ack."""
        return not self._ready and not self._unacked


@dataclass
class KillSwitch:
    """Kills the worker at chosen points (1-based message counts). Each point fires once.

    ``before_commit`` models a kill while the order is being saved;
    ``after_commit`` models a kill after the save but before the ack.
    """

    before_commit: set[int] = field(default_factory=set)
    after_commit: set[int] = field(default_factory=set)
    handled: int = 0

    def start_message(self) -> None:
        """Count one more message being handled."""
        self.handled += 1

    def check(self, kill_points: set[int]) -> None:
        """Raise ProcessKilled if this message is a kill point, then disarm it."""
        if self.handled in kill_points:
            kill_points.discard(self.handled)
            raise ProcessKilled(f"worker killed at message {self.handled}")


def open_order_store() -> sqlite3.Connection:
    """In-memory orders table; order_id is the primary key."""
    connection = sqlite3.connect(":memory:")
    connection.execute("CREATE TABLE orders (order_id TEXT PRIMARY KEY, total_cents INTEGER)")
    return connection


def order_message(number: int) -> str:
    """JSON body of one order event."""
    return json.dumps({"order_id": f"ord-{number:05d}", "total_cents": 1000 + number})


def save_order(connection: sqlite3.Connection, body: str) -> None:
    """Commit one order. Insert-if-absent, so a redelivered message is a no-op."""
    order = json.loads(body)
    with connection:  # one transaction: committed on exit
        connection.execute(
            "INSERT INTO orders (order_id, total_cents) VALUES (?, ?) "
            "ON CONFLICT (order_id) DO NOTHING",
            (order["order_id"], order["total_cents"]))


def naive_consume(broker: InMemoryBroker, store: sqlite3.Connection, kill: KillSwitch) -> None:
    """Original worker: ack the batch on receive, then process it."""
    while True:
        batch = broker.receive(BATCH_SIZE, ack_on_receive=True)
        if not batch:
            return
        for delivery in batch:
            kill.start_message()
            kill.check(kill.before_commit)
            save_order(store, delivery.body)
            kill.check(kill.after_commit)


def consume(broker: InMemoryBroker, store: sqlite3.Connection, kill: KillSwitch) -> None:
    """Fixed worker: ack each message only after its order is committed."""
    while True:
        batch = broker.receive(BATCH_SIZE, ack_on_receive=False)
        if not batch:
            return
        for delivery in batch:
            kill.start_message()
            kill.check(kill.before_commit)
            save_order(store, delivery.body)
            kill.check(kill.after_commit)
            # Ack last: until here the broker still owns the message.
            broker.ack(delivery.tag)


Consumer = Callable[[InMemoryBroker, sqlite3.Connection, KillSwitch], None]


def run_with_restarts(broker: InMemoryBroker, store: sqlite3.Connection,
                      consumer: Consumer, kill: KillSwitch, max_restarts: int = 100) -> int:
    """Run the worker, restarting it after each kill (like Kubernetes would). Returns restarts."""
    restarts = 0
    while restarts <= max_restarts:
        try:
            consumer(broker, store, kill)
        except ProcessKilled:
            broker.connection_lost()
            restarts += 1
            continue
        return restarts
    raise RuntimeError("worker kept dying")


def stored_order_ids(store: sqlite3.Connection) -> set[str]:
    """All order ids that made it into the database."""
    rows = store.execute("SELECT order_id FROM orders").fetchall()
    return {row[0] for row in rows}
