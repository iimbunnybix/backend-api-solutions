import threading
import unittest

from solution import IdempotencyConflict, IdempotencyKeyReuse, IdempotencyStore, PaymentService


class IdempotencyTests(unittest.TestCase):
    def test_retry_returns_same_response_and_charges_once(self):
        svc = PaymentService(IdempotencyStore())
        body = {"order": "A1", "amount": 499}
        first = svc.create_payment("key-1", body)
        second = svc.create_payment("key-1", body)
        self.assertEqual(first, second)
        self.assertEqual(len(svc.charges), 1)

    def test_different_keys_are_different_payments(self):
        svc = PaymentService(IdempotencyStore())
        svc.create_payment("k1", {"amount": 1})
        svc.create_payment("k2", {"amount": 1})
        self.assertEqual(len(svc.charges), 2)

    def test_key_reuse_with_different_body_rejected(self):
        svc = PaymentService(IdempotencyStore())
        svc.create_payment("k", {"amount": 100})
        with self.assertRaises(IdempotencyKeyReuse):
            svc.create_payment("k", {"amount": 999})

    def test_concurrent_duplicates_charge_once(self):
        svc = PaymentService(IdempotencyStore(), charge_delay=0.05)
        results, conflicts = [], []

        def call():
            try:
                results.append(svc.create_payment("same", {"amount": 10}))
            except IdempotencyConflict:
                conflicts.append(1)

        threads = [threading.Thread(target=call) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len(svc.charges), 1)
        self.assertEqual(len(results) + len(conflicts), 10)

    def test_failed_charge_can_be_retried(self):
        store = IdempotencyStore()
        calls = []

        def flaky(payload):
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError("gateway timeout")
            return {"ok": True}

        with self.assertRaises(RuntimeError):
            store.execute("k", {"a": 1}, flaky)
        self.assertEqual(store.execute("k", {"a": 1}, flaky), {"ok": True})

    def test_keys_expire_after_ttl(self):
        now = [0.0]
        svc = PaymentService(IdempotencyStore(ttl_seconds=10, clock=lambda: now[0]))
        svc.create_payment("k", {"amount": 1})
        now[0] = 11
        svc.create_payment("k", {"amount": 1})
        self.assertEqual(len(svc.charges), 2)

    def test_slow_charge_outliving_ttl_still_completes(self):
        now = [0.0]
        store = IdempotencyStore(ttl_seconds=10, clock=lambda: now[0])

        def slow_charge(payload):
            now[0] += 11  # the charge takes longer than the TTL
            # Another request arrives meanwhile and triggers a purge.
            store.execute("other", {"a": 1}, lambda p: {"ok": 1})
            return {"charged": True}

        self.assertEqual(store.execute("k", {"amount": 5}, slow_charge), {"charged": True})

    def test_in_progress_key_is_not_purged_by_ttl(self):
        now = [0.0]
        store = IdempotencyStore(ttl_seconds=10, clock=lambda: now[0])

        def slow_charge(payload):
            now[0] += 11
            # A retry with the same key during the charge must still be rejected.
            with self.assertRaises(IdempotencyConflict):
                store.execute("k", {"amount": 5}, lambda p: {"charged": "twice"})
            return {"charged": True}

        store.execute("k", {"amount": 5}, slow_charge)


if __name__ == "__main__":
    unittest.main()
