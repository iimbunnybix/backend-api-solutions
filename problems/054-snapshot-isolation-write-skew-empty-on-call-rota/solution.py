"""Two doctors went off call at the same moment and left a night shift with nobody on call.

The rota service checked "is at least one other doctor still on call?" and then
marked the requester off call, inside a transaction running under snapshot
isolation (PostgreSQL's REPEATABLE READ behaves this way). Two concurrent
requests each saw the other doctor still on call in their snapshot, wrote
*different* rows, and both committed: a write skew. Snapshot isolation only
detects two transactions writing the same row. This version makes every
leave request also write one guard row per shift, which turns the hidden
read dependency into a real write-write conflict, and retries on conflict.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

ON_CALL = "on_call"
OFF_CALL = "off_call"
MIN_DOCTORS_ON_CALL = 1
MAX_ATTEMPTS = 3


class SerializationConflict(Exception):
    """Commit refused: another transaction committed a write to the same row first."""


class NotEnoughCover(Exception):
    """The request would leave the shift with fewer than MIN_DOCTORS_ON_CALL doctors."""


@dataclass
class VersionedValue:
    value: object
    version: int


class SnapshotStore:
    """Tiny multi-version store with snapshot isolation and first-committer-wins."""

    def __init__(self) -> None:
        self.committed: dict[str, VersionedValue] = {}

    def load(self, key: str, value: object) -> None:
        """Seed committed data before any transaction runs."""
        self.committed[key] = VersionedValue(value, 0)

    def begin(self) -> "Transaction":
        """Start a transaction that sees the data as committed right now."""
        return Transaction(self, dict(self.committed))


class Transaction:
    def __init__(self, store: SnapshotStore, snapshot: dict[str, VersionedValue]) -> None:
        self.store = store
        self.snapshot = snapshot
        self.writes: dict[str, object] = {}

    def read(self, key: str) -> object:
        """Own writes first, otherwise the value from the snapshot taken at begin()."""
        if key in self.writes:
            return self.writes[key]
        return self.snapshot[key].value

    def keys_with_prefix(self, prefix: str) -> list[str]:
        return sorted(key for key in self.snapshot if key.startswith(prefix))

    def write(self, key: str, value: object) -> None:
        self.writes[key] = value

    def commit(self) -> None:
        """Apply writes unless a written row changed since our snapshot.

        Only rows this transaction *wrote* are checked. Rows it merely read are
        not, which is exactly why write skew slips through snapshot isolation.
        """
        for key in self.writes:
            seen = self.snapshot.get(key)
            current = self.store.committed.get(key)
            if current is not None and (seen is None or current.version != seen.version):
                raise SerializationConflict(key)
        for key, value in self.writes.items():
            old = self.store.committed.get(key)
            next_version = 1 if old is None else old.version + 1
            self.store.committed[key] = VersionedValue(value, next_version)


def doctor_key(shift: str, doctor: str) -> str:
    return f"shift:{shift}:doctor:{doctor}"


def guard_key(shift: str) -> str:
    """One row per shift that every leave request writes, so they conflict."""
    return f"shift:{shift}:guard"


def count_on_call(txn: Transaction, shift: str) -> int:
    count = 0
    for key in txn.keys_with_prefix(f"shift:{shift}:doctor:"):
        if txn.read(key) == ON_CALL:
            count += 1
    return count


def naive_go_off_call(txn: Transaction, shift: str, doctor: str) -> None:
    """Original logic: check the other doctors, then write only our own row."""
    if count_on_call(txn, shift) - 1 < MIN_DOCTORS_ON_CALL:
        raise NotEnoughCover(shift)
    txn.write(doctor_key(shift, doctor), OFF_CALL)


def go_off_call(txn: Transaction, shift: str, doctor: str) -> None:
    """Fixed logic: also bump the shift's guard row before deciding.

    Two concurrent requests for the same shift now both write the guard row,
    so the second commit fails with SerializationConflict instead of silently
    succeeding. This is the "materialise the conflict" fix; in SQL it is
    SELECT ... FOR UPDATE on the shift row, or running at SERIALIZABLE.
    """
    txn.write(guard_key(shift), int(txn.read(guard_key(shift))) + 1)
    naive_go_off_call(txn, shift, doctor)


def run_with_retry(store: SnapshotStore, action: Callable[[Transaction], None]) -> int:
    """Run `action` in a transaction, retrying on conflict; return attempts used.

    A retry starts a fresh snapshot, so it sees the winner's commit and makes
    its decision again on current data (here: usually NotEnoughCover).
    """
    for attempt in range(1, MAX_ATTEMPTS + 1):
        txn = store.begin()
        action(txn)
        try:
            txn.commit()
            return attempt
        except SerializationConflict:
            if attempt == MAX_ATTEMPTS:
                raise
    raise AssertionError("unreachable")


def build_rota(shift: str, doctors: list[str]) -> SnapshotStore:
    """A store with every doctor on call for `shift` and its guard row."""
    store = SnapshotStore()
    for doctor in doctors:
        store.load(doctor_key(shift, doctor), ON_CALL)
    store.load(guard_key(shift), 0)
    return store
