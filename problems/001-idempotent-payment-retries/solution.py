"""Idempotency-key middleware for a payment endpoint."""
from __future__ import annotations

import hashlib
import json
import threading
import time
from dataclasses import dataclass, field
from typing import Callable


class IdempotencyConflict(Exception):
    """Same key is still being processed by another request (HTTP 409)."""


class IdempotencyKeyReuse(Exception):
    """Same key sent with a different payload (HTTP 422)."""


@dataclass
class _Entry:
    fingerprint: str
    status: str                      # IN_PROGRESS | DONE
    response: dict | None = None
    created_at: float = field(default_factory=time.monotonic)


def fingerprint(payload: dict) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


class IdempotencyStore:
    def __init__(self, ttl_seconds: float = 86_400, clock: Callable[[], float] = time.monotonic):
        self._ttl = ttl_seconds
        self._clock = clock
        self._entries: dict[str, _Entry] = {}
        self._lock = threading.Lock()

    def _purge_expired(self) -> None:
        now = self._clock()
        # Only finished entries may expire: purging an IN_PROGRESS key would let a
        # concurrent retry charge again and break the completion step below.
        # For DONE entries created_at is reset to the completion time.
        expired = [k for k, e in self._entries.items()
                   if e.status == "DONE" and now - e.created_at > self._ttl]
        for k in expired:
            del self._entries[k]

    def execute(self, key: str, payload: dict, handler: Callable[[dict], dict]) -> dict:
        fp = fingerprint(payload)
        with self._lock:                                   # atomic claim
            self._purge_expired()
            entry = self._entries.get(key)
            if entry is not None:
                if entry.fingerprint != fp:
                    raise IdempotencyKeyReuse(key)
                if entry.status == "IN_PROGRESS":
                    raise IdempotencyConflict(key)
                return entry.response                      # replay stored result
            self._entries[key] = _Entry(fp, "IN_PROGRESS", created_at=self._clock())

        try:
            response = handler(payload)                    # runs outside the lock
        except Exception:
            with self._lock:                               # failed: allow a clean retry
                self._entries.pop(key, None)
            raise

        with self._lock:
            # TTL counts from completion, so a slow charge still gets the full window.
            self._entries[key] = _Entry(fp, "DONE", response, created_at=self._clock())
        return response


class PaymentService:
    """Toy payment processor that counts real charges."""

    def __init__(self, store: IdempotencyStore, charge_delay: float = 0.0):
        self.store = store
        self.charges: list[dict] = []
        self._delay = charge_delay
        self._lock = threading.Lock()

    def _charge(self, payload: dict) -> dict:
        time.sleep(self._delay)
        with self._lock:
            self.charges.append(payload)
            return {"payment_id": f"pay_{len(self.charges)}", "status": "succeeded",
                    "amount": payload["amount"]}

    def create_payment(self, idempotency_key: str, payload: dict) -> dict:
        return self.store.execute(idempotency_key, payload, self._charge)
