import random
import unittest

from solution import (Lock, LockManager, Scheduler, lock_in_order, naive_transfer,
                      ordered_transfer)


def workload(seed, n_txns=400, n_accounts=10, amount_max=50):
    rng = random.Random(seed)
    pairs = []
    for _ in range(n_txns):
        a, b = rng.sample(range(n_accounts), 2)
        pairs.append((a, b, rng.randint(1, amount_max)))
    return pairs


def fresh_db(n=10, balance=10_000):
    return {i: balance for i in range(n)}


class ReproduceDeadlock(unittest.TestCase):
    def test_opposite_transfers_deadlock(self):
        db = fresh_db(2)
        res = Scheduler(db).run([naive_transfer(0, 1, 10), naive_transfer(1, 0, 5)])
        self.assertEqual(res.deadlocks, 1)
        self.assertEqual(res.committed, 1)
        self.assertEqual(len(res.failed_deadlock), 1)   # the user saw a 500
        self.assertEqual(sum(db.values()), 20_000)      # victim rolled back

    def test_random_workload_without_retry_fails_requests(self):
        db = fresh_db()
        res = Scheduler(db).run([naive_transfer(*p) for p in workload(1)])
        self.assertGreater(res.deadlocks, 5)            # 11 of 400 with seed 1
        self.assertEqual(len(res.failed_deadlock), res.deadlocks)
        self.assertEqual(sum(db.values()), 100_000)


    def test_deadlock_rate_across_seeds(self):
        deadlocks = 0
        for seed in range(20):
            res = Scheduler(fresh_db()).run([naive_transfer(*p) for p in workload(seed)])
            deadlocks += res.deadlocks
        self.assertEqual(deadlocks, 137)               # 1.7% of 8,000 transfers


class OrderedLocking(unittest.TestCase):
    def test_opposite_transfers_both_commit(self):
        db = fresh_db(2)
        res = Scheduler(db).run([ordered_transfer(0, 1, 10), ordered_transfer(1, 0, 5)])
        self.assertEqual((res.deadlocks, res.committed), (0, 2))
        self.assertEqual(db, {0: 9_995, 1: 10_005})

    def test_random_workloads_never_deadlock(self):
        for seed in range(20):
            db = fresh_db()
            pairs = workload(seed)
            res = Scheduler(db).run([ordered_transfer(*p) for p in pairs], concurrency=16)
            self.assertEqual(res.deadlocks, 0, seed)
            self.assertEqual(res.committed, len(pairs))
            self.assertEqual(sum(db.values()), 100_000)

    def test_result_matches_serial_execution(self):
        pairs = workload(3)
        serial = fresh_db()
        for a, b, amt in pairs:
            serial[a] -= amt
            serial[b] += amt
        db = fresh_db()
        Scheduler(db).run([ordered_transfer(*p) for p in pairs])
        self.assertEqual(db, serial)

    def test_lock_in_order_dedupes_and_sorts(self):
        self.assertEqual(lock_in_order([7, 2, 7, 5]), [Lock(2), Lock(5), Lock(7)])

    def test_same_account_rejected(self):
        with self.assertRaises(ValueError):
            ordered_transfer(1, 1, 5)

    def test_zero_amount_rejected_before_any_lock_is_taken(self):
        with self.assertRaises(ValueError):
            ordered_transfer("A", "B", 0)

    def test_negative_amount_rejected_before_any_lock_is_taken(self):
        with self.assertRaises(ValueError):
            ordered_transfer("A", "B", -5)

    def test_batch_with_invalid_amount_cannot_strand_valid_transfers(self):
        db = {"A": 100, "B": 100, "C": 100}
        with self.assertRaises(ValueError):
            Scheduler(db).run([ordered_transfer("A", "B", 10),
                               ordered_transfer("B", "C", 0)])
        # Rejected at construction, so no transaction ever started.
        self.assertEqual(db, {"A": 100, "B": 100, "C": 100})


class RetryAndEdgeCases(unittest.TestCase):
    def test_retry_hides_deadlocks_but_they_still_happen(self):
        db = fresh_db()
        pairs = workload(1)
        res = Scheduler(db, max_retries=10).run([naive_transfer(*p) for p in pairs])
        self.assertGreater(res.deadlocks, 0)
        self.assertEqual(res.failed_deadlock, [])
        self.assertEqual(res.committed, len(pairs))
        self.assertEqual(sum(db.values()), 100_000)

    def test_insufficient_funds_is_not_retried(self):
        db = {0: 5, 1: 0}
        res = Scheduler(db, max_retries=3).run([ordered_transfer(0, 1, 10)])
        self.assertEqual(res.failed_business, [0])
        self.assertEqual(db, {0: 5, 1: 0})

    def test_three_way_cycle_detected(self):
        db = fresh_db(3)
        txns = [naive_transfer(0, 1, 1), naive_transfer(1, 2, 1), naive_transfer(2, 0, 1)]
        res = Scheduler(db, max_retries=1).run(txns)
        self.assertEqual(res.deadlocks, 1)
        self.assertEqual(res.committed, 3)

    def test_youngest_transaction_is_victim(self):
        db = fresh_db(2)
        res = Scheduler(db).run([naive_transfer(0, 1, 1), naive_transfer(1, 0, 1)])
        self.assertEqual(res.failed_deadlock, [1])

    def test_lock_manager_cycle_detection(self):
        lm = LockManager()
        self.assertTrue(lm.acquire(1, "a"))
        self.assertTrue(lm.acquire(2, "b"))
        self.assertFalse(lm.acquire(1, "b"))
        self.assertIsNone(lm.find_cycle(1))
        self.assertFalse(lm.acquire(2, "a"))
        self.assertEqual(sorted(lm.find_cycle(2)), [1, 2])
        lm.release_all(1)
        self.assertTrue(lm.acquire(2, "a"))


if __name__ == "__main__":
    unittest.main()
