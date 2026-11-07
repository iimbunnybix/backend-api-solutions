"""Deadlock-free money transfers: lock rows in a global order, retry victims.

The module contains a small, deterministic model of a database's row-lock
manager (exclusive locks, a wait-for graph and a deadlock detector that aborts
the youngest transaction in a cycle, like PostgreSQL and InnoDB do) plus the
two versions of the transfer code that ran against it:

* ``naive_transfer`` locks the source row and then the destination row, the
  order the code happens to read them in. Two opposite transfers between the
  same accounts (A->B and B->A) can each hold one lock and wait for the other.
* ``ordered_transfer`` locks both rows in ascending key order
  (``SELECT ... WHERE id IN (...) ORDER BY id FOR UPDATE``), so no cycle of
  waits can form.

``Scheduler`` interleaves transactions one step at a time in round-robin
order, so tests can reproduce a deadlock exactly instead of hoping a thread
race happens. ``max_retries`` models the application's retry-on-deadlock
wrapper, which is still needed for deadlocks that ordering cannot prevent
(for example locks taken by other services).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Generator, Hashable, Iterable

Key = Hashable


@dataclass(frozen=True)
class Lock:
    """Request an exclusive lock on ``key`` (``SELECT ... FOR UPDATE``)."""
    key: Key


class InsufficientFunds(Exception):
    """Business-rule failure: not retryable, the transaction just aborts."""


# A transaction is a generator that yields Lock requests and finally returns a
# dict of {key: new_value} writes, applied atomically on commit.
TxnGen = Generator[Lock, None, dict]
TxnFactory = Callable[[dict], TxnGen]


def lock_in_order(keys: Iterable[Key]) -> list[Lock]:
    """Lock requests for ``keys`` with duplicates removed, in ascending order.

    Every code path that locks more than one row must use the same order; a
    single path that locks "in the order the request listed them" brings the
    deadlock back.
    """
    return [Lock(k) for k in sorted(set(keys))]


def _apply_transfer(db: dict, src: Key, dst: Key, amount: int) -> dict:
    if amount <= 0:
        raise ValueError("amount must be positive")
    if db[src] < amount:
        raise InsufficientFunds(f"{src} has {db[src]}, needs {amount}")
    return {src: db[src] - amount, dst: db[dst] + amount}


def _validate_amount(amount: int) -> None:
    """Reject a non-positive amount up front.

    Checking it inside the transaction would raise after locks are held,
    and the error would escape Scheduler.run and abort the whole batch.
    """
    if amount <= 0:
        raise ValueError("amount must be positive")


def naive_transfer(src: Key, dst: Key, amount: int) -> TxnFactory:
    """The original code: lock source, then destination."""
    _validate_amount(amount)
    def txn(db: dict) -> TxnGen:
        yield Lock(src)
        yield Lock(dst)
        return _apply_transfer(db, src, dst, amount)
    return txn


def ordered_transfer(src: Key, dst: Key, amount: int) -> TxnFactory:
    """The fix: lock both rows in ascending key order, then apply."""
    if src == dst:
        raise ValueError("cannot transfer to the same account")
    _validate_amount(amount)

    def txn(db: dict) -> TxnGen:
        for req in lock_in_order([src, dst]):
            yield req
        return _apply_transfer(db, src, dst, amount)
    return txn


class LockManager:
    """Exclusive row locks with a wait-for graph."""

    def __init__(self) -> None:
        self.owner: dict[Key, int] = {}
        self.waits_for: dict[int, int] = {}

    def acquire(self, txn: int, key: Key) -> bool:
        holder = self.owner.get(key)
        if holder is None or holder == txn:
            self.owner[key] = txn
            self.waits_for.pop(txn, None)
            return True
        self.waits_for[txn] = holder
        return False

    def release_all(self, txn: int) -> None:
        for key in [k for k, t in self.owner.items() if t == txn]:
            del self.owner[key]
        self.waits_for.pop(txn, None)

    def find_cycle(self, start: int) -> list[int] | None:
        """Return the transactions in a wait cycle through ``start``, if any."""
        path, node = [start], self.waits_for.get(start)
        while node is not None:
            if node == start:
                return path
            if node in path:          # cycle not involving start
                return None
            path.append(node)
            node = self.waits_for.get(node)
        return None


@dataclass
class _Run:
    priority: int                 # original start order; lower = older
    txn_id: int
    factory: TxnFactory
    gen: TxnGen
    attempts: int = 1
    pending: Key | None = None


@dataclass
class RunResult:
    committed: int = 0
    deadlocks: int = 0            # deadlock aborts (each one retried or failed)
    failed_deadlock: list[int] = field(default_factory=list)  # gave up
    failed_business: list[int] = field(default_factory=list)  # e.g. no funds
    ticks: int = 0


class Scheduler:
    """Interleave transactions step by step, detect deadlocks, retry victims."""

    def __init__(self, db: dict, max_retries: int = 0, max_ticks: int = 1_000_000):
        self.db = db
        self.locks = LockManager()
        self.max_retries = max_retries
        self.max_ticks = max_ticks
        self._next_id = 0

    def _start(self, priority: int, factory: TxnFactory, attempts: int = 1) -> _Run:
        self._next_id += 1
        return _Run(priority, self._next_id, factory, factory(self.db), attempts)

    def run(self, factories: list[TxnFactory], concurrency: int = 8) -> RunResult:
        """Run ``factories`` with up to ``concurrency`` transactions in flight."""
        result = RunResult()
        queue = list(enumerate(factories))
        active: list[_Run] = []

        def finish(run: _Run) -> None:
            self.locks.release_all(run.txn_id)
            active.remove(run)

        while queue or active:
            while queue and len(active) < concurrency:
                prio, f = queue.pop(0)
                active.append(self._start(prio, f))
            result.ticks += 1
            if result.ticks > self.max_ticks:
                raise RuntimeError("scheduler made no progress")
            for run in list(active):
                if run not in active:          # aborted earlier this tick
                    continue
                if run.pending is not None:
                    if not self.locks.acquire(run.txn_id, run.pending):
                        # The lock may now be held by a different transaction
                        # than before, so this new wait can close a cycle.
                        self._check_deadlock(run, active, result, finish)
                        continue
                    run.pending = None
                try:
                    req = next(run.gen)
                except StopIteration as stop:
                    self.db.update(stop.value or {})
                    result.committed += 1
                    finish(run)
                    continue
                except InsufficientFunds:
                    result.failed_business.append(run.priority)
                    finish(run)
                    continue
                if self.locks.acquire(run.txn_id, req.key):
                    continue
                run.pending = req.key
                self._check_deadlock(run, active, result, finish)
        return result

    def _check_deadlock(self, run, active, result, finish) -> None:
        cycle = self.locks.find_cycle(run.txn_id)
        if cycle:
            self._abort_victim(cycle, active, result, finish)

    def _abort_victim(self, cycle, active, result, finish) -> None:
        by_id = {r.txn_id: r for r in active}
        victim = max((by_id[t] for t in cycle), key=lambda r: r.priority)
        victim.gen.close()                     # roll back: writes never applied
        finish(victim)
        result.deadlocks += 1
        if victim.attempts <= self.max_retries:
            # Keep the original priority so a retried transaction ages and is
            # not chosen as the victim forever (no starvation).
            active.append(self._start(victim.priority, victim.factory,
                                      victim.attempts + 1))
        else:
            result.failed_deadlock.append(victim.priority)
