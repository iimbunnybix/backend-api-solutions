import unittest

from solution import SessionConflict, SessionStore, run_with_retry

SESSION_ID = "sess-1"


def store_with_cart(items):
    """A store whose session already holds a cart and a locale."""
    store = SessionStore()
    view = store.open(SESSION_ID)
    view.set("cart", list(items))
    view.set("locale", "en-IN")
    store.commit(view)
    return store


def add_to_cart(item):
    """Handler that appends one item to the cart (read, then set)."""
    def handler(view):
        view.set("cart", view.get("cart", []) + [item])
    return handler


class ReproduceProblem(unittest.TestCase):
    def test_parallel_whole_session_saves_lose_the_added_item(self):
        store = store_with_cart(["tea"])
        add_request = store.snapshot(SESSION_ID)
        viewed_request = store.snapshot(SESSION_ID)
        add_request["cart"].append("kettle")
        store.naive_save(SESSION_ID, add_request)
        viewed_request["recently_viewed"] = ["toaster"]
        store.naive_save(SESSION_ID, viewed_request)
        self.assertEqual(store.snapshot(SESSION_ID)["cart"], ["tea"])


class FixSavesOnlyChangedKeys(unittest.TestCase):
    def test_changes_to_different_keys_both_survive(self):
        store = store_with_cart(["tea"])
        add_request = store.open(SESSION_ID)
        viewed_request = store.open(SESSION_ID)
        add_request.set("cart", add_request.get("cart") + ["kettle"])
        store.commit(add_request)
        viewed_request.set("recently_viewed", ["toaster"])
        store.commit(viewed_request)
        session = store.snapshot(SESSION_ID)
        self.assertEqual(session["cart"], ["tea", "kettle"])
        self.assertEqual(session["recently_viewed"], ["toaster"])

    def test_read_only_request_writes_nothing(self):
        store = store_with_cart(["tea"])
        reader = store.open(SESSION_ID)
        writer = store.open(SESSION_ID)
        writer.set("cart", ["tea", "kettle"])
        store.commit(writer)
        reader.get("cart")
        store.commit(reader)
        self.assertEqual(store.snapshot(SESSION_ID)["cart"], ["tea", "kettle"])

    def test_deleted_key_is_removed(self):
        store = store_with_cart(["tea"])
        view = store.open(SESSION_ID)
        view.delete("locale")
        store.commit(view)
        self.assertNotIn("locale", store.snapshot(SESSION_ID))

    def test_mutating_a_snapshot_does_not_change_the_store(self):
        store = store_with_cart(["tea"])
        store.snapshot(SESSION_ID)["cart"].append("kettle")
        self.assertEqual(store.snapshot(SESSION_ID)["cart"], ["tea"])


class FixDetectsSameKeyConflicts(unittest.TestCase):
    def test_second_write_to_the_same_key_is_refused(self):
        store = store_with_cart([])
        first = store.open(SESSION_ID)
        second = store.open(SESSION_ID)
        add_to_cart("kettle")(first)
        add_to_cart("toaster")(second)
        store.commit(first)
        with self.assertRaises(SessionConflict):
            store.commit(second)
        self.assertEqual(store.snapshot(SESSION_ID)["cart"], ["kettle"])

    def test_refused_commit_writes_none_of_its_keys(self):
        store = store_with_cart([])
        first = store.open(SESSION_ID)
        second = store.open(SESSION_ID)
        first.set("cart", ["kettle"])
        store.commit(first)
        second.set("cart", ["toaster"])
        second.set("coupon", "SAVE10")
        with self.assertRaises(SessionConflict):
            store.commit(second)
        self.assertNotIn("coupon", store.snapshot(SESSION_ID))

    def test_retry_reapplies_the_change_on_fresh_data(self):
        store = store_with_cart([])
        calls = []

        def add_toaster_while_another_request_adds_kettle(view):
            if not calls:
                run_with_retry(store, SESSION_ID, add_to_cart("kettle"))
            calls.append(1)
            add_to_cart("toaster")(view)

        run_with_retry(store, SESSION_ID, add_toaster_while_another_request_adds_kettle)
        self.assertEqual(store.snapshot(SESSION_ID)["cart"], ["kettle", "toaster"])
        self.assertEqual(len(calls), 2)

    def test_retry_gives_up_after_max_attempts(self):
        store = store_with_cart([])

        def always_interrupted(view):
            run_with_retry(store, SESSION_ID, add_to_cart("other"))
            add_to_cart("toaster")(view)

        with self.assertRaises(SessionConflict):
            run_with_retry(store, SESSION_ID, always_interrupted, max_attempts=2)


if __name__ == "__main__":
    unittest.main()
