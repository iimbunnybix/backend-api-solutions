import unittest

from solution import (NotEnoughCover, SerializationConflict, build_rota, count_on_call,
                      go_off_call, naive_go_off_call, run_with_retry, doctor_key, ON_CALL)

SHIFT = "2026-11-14-night"


def two_doctor_rota():
    return build_rota(SHIFT, ["alice", "bob"])


def on_call_now(store) -> int:
    return count_on_call(store.begin(), SHIFT)


class ReproduceProblem(unittest.TestCase):
    def test_concurrent_requests_leave_nobody_on_call(self):
        store = two_doctor_rota()
        alice_txn, bob_txn = store.begin(), store.begin()
        naive_go_off_call(alice_txn, SHIFT, "alice")
        naive_go_off_call(bob_txn, SHIFT, "bob")
        alice_txn.commit()
        bob_txn.commit()
        self.assertEqual(on_call_now(store), 0)

    def test_sequential_requests_were_always_safe(self):
        store = two_doctor_rota()
        run_with_retry(store, lambda txn: naive_go_off_call(txn, SHIFT, "alice"))
        with self.assertRaises(NotEnoughCover):
            run_with_retry(store, lambda txn: naive_go_off_call(txn, SHIFT, "bob"))


class FixSnapshotStore(unittest.TestCase):
    def test_writes_to_the_same_row_conflict(self):
        store = two_doctor_rota()
        first, second = store.begin(), store.begin()
        first.write(doctor_key(SHIFT, "alice"), "x")
        second.write(doctor_key(SHIFT, "alice"), "y")
        first.commit()
        with self.assertRaises(SerializationConflict):
            second.commit()

    def test_transaction_reads_its_own_writes(self):
        txn = two_doctor_rota().begin()
        txn.write(doctor_key(SHIFT, "alice"), "off_call")
        self.assertEqual(count_on_call(txn, SHIFT), 1)


class FixGuardRow(unittest.TestCase):
    def test_second_concurrent_request_conflicts_instead_of_committing(self):
        store = two_doctor_rota()
        alice_txn, bob_txn = store.begin(), store.begin()
        go_off_call(alice_txn, SHIFT, "alice")
        go_off_call(bob_txn, SHIFT, "bob")
        alice_txn.commit()
        with self.assertRaises(SerializationConflict):
            bob_txn.commit()
        self.assertEqual(on_call_now(store), 1)

    def test_retry_sees_the_winner_and_refuses_for_lack_of_cover(self):
        store = two_doctor_rota()
        alice_txn = store.begin()
        go_off_call(alice_txn, SHIFT, "alice")
        alice_txn.commit()
        with self.assertRaises(NotEnoughCover):
            run_with_retry(store, lambda txn: go_off_call(txn, SHIFT, "bob"))

    def test_with_three_doctors_the_conflicting_request_succeeds_on_retry(self):
        store = build_rota(SHIFT, ["alice", "bob", "carol"])
        alice_txn, bob_txn = store.begin(), store.begin()
        go_off_call(alice_txn, SHIFT, "alice")
        go_off_call(bob_txn, SHIFT, "bob")
        alice_txn.commit()
        with self.assertRaises(SerializationConflict):
            bob_txn.commit()
        run_with_retry(store, lambda txn: go_off_call(txn, SHIFT, "bob"))
        self.assertEqual(on_call_now(store), 1)

    def test_other_shifts_do_not_conflict(self):
        store = two_doctor_rota()
        store.load(doctor_key("other", "dan"), ON_CALL)
        store.load(doctor_key("other", "erin"), ON_CALL)
        store.load("shift:other:guard", 0)
        night_txn, other_txn = store.begin(), store.begin()
        go_off_call(night_txn, SHIFT, "alice")
        go_off_call(other_txn, "other", "dan")
        night_txn.commit()
        other_txn.commit()
        self.assertEqual(on_call_now(store), 1)

    def test_last_doctor_cannot_go_off_call(self):
        store = build_rota(SHIFT, ["alice"])
        with self.assertRaises(NotEnoughCover):
            run_with_retry(store, lambda txn: go_off_call(txn, SHIFT, "alice"))


if __name__ == "__main__":
    unittest.main()
