import json
import unittest

from solution import (
    CopyingProductCache,
    FrozenProductCache,
    NaiveProductCache,
    freeze,
    member_price_view,
    naive_member_price_view,
    naive_public_view,
    public_view,
    thaw,
)

LAMP_PRICE_CENTS = 2000
MEMBER_DISCOUNT_PERCENT = 10


class FakeCatalogDb:
    """Returns a fresh product dict per load and counts how often it is called."""

    def __init__(self):
        self.loads = 0

    def load(self, sku):
        self.loads += 1
        return {"sku": sku, "name": "Desk lamp", "price_cents": LAMP_PRICE_CENTS,
                "cost_cents": 900, "tags": ["lighting", "office"]}


class ReproduceProblem(unittest.TestCase):
    def setUp(self):
        self.cache = NaiveProductCache(FakeCatalogDb().load)

    def test_member_discount_leaks_to_next_guest(self):
        naive_member_price_view(self.cache.get("lamp"), MEMBER_DISCOUNT_PERCENT)
        self.assertEqual(self.cache.get("lamp")["price_cents"], 1800)

    def test_discount_compounds_on_every_member_request(self):
        for _ in range(2):
            naive_member_price_view(self.cache.get("lamp"), MEMBER_DISCOUNT_PERCENT)
        self.assertEqual(self.cache.get("lamp")["price_cents"], 1620)

    def test_public_view_removes_field_for_admins_too(self):
        naive_public_view(self.cache.get("lamp"))
        self.assertNotIn("cost_cents", self.cache.get("lamp"))

    def test_nested_list_change_leaks(self):
        self.cache.get("lamp")["tags"].append("sale")
        self.assertIn("sale", self.cache.get("lamp")["tags"])


class FixFrozenSnapshots(unittest.TestCase):
    def setUp(self):
        self.db = FakeCatalogDb()
        self.cache = FrozenProductCache(self.db.load)

    def test_writing_a_field_fails_loudly(self):
        with self.assertRaises(TypeError):
            self.cache.get("lamp")["price_cents"] = 1

    def test_removing_a_field_fails_loudly(self):
        with self.assertRaises(TypeError):
            del self.cache.get("lamp")["cost_cents"]

    def test_nested_list_is_read_only(self):
        with self.assertRaises(AttributeError):
            self.cache.get("lamp")["tags"].append("sale")

    def test_original_buggy_handler_now_crashes_instead_of_corrupting(self):
        with self.assertRaises(TypeError):
            naive_member_price_view(self.cache.get("lamp"), MEMBER_DISCOUNT_PERCENT)
        self.assertEqual(self.cache.get("lamp")["price_cents"], LAMP_PRICE_CENTS)

    def test_member_view_is_discounted_and_cache_is_not(self):
        view = member_price_view(self.cache.get("lamp"), MEMBER_DISCOUNT_PERCENT)
        self.assertEqual(view["price_cents"], 1800)
        self.assertEqual(self.cache.get("lamp")["price_cents"], LAMP_PRICE_CENTS)

    def test_public_view_hides_cost_but_cache_keeps_it(self):
        self.assertNotIn("cost_cents", public_view(self.cache.get("lamp")))
        self.assertEqual(self.cache.get("lamp")["cost_cents"], 900)

    def test_loader_object_changed_later_does_not_affect_cache(self):
        loaded = {"price_cents": 5, "tags": ["a"]}
        cache = FrozenProductCache(lambda sku: loaded)
        cache.get("x")
        loaded["price_cents"] = 999
        loaded["tags"].append("b")
        self.assertEqual(thaw(cache.get("x")), {"price_cents": 5, "tags": ["a"]})

    def test_still_caches_and_returns_same_snapshot(self):
        first = self.cache.get("lamp")
        self.assertIs(self.cache.get("lamp"), first)
        self.assertEqual(self.db.loads, 1)

    def test_frozen_value_must_be_thawed_before_json(self):
        with self.assertRaises(TypeError):
            json.dumps(self.cache.get("lamp"))
        self.assertIn('"tags": ["lighting", "office"]', json.dumps(thaw(self.cache.get("lamp"))))


class FixCopyOnRead(unittest.TestCase):
    def setUp(self):
        self.db = FakeCatalogDb()
        self.cache = CopyingProductCache(self.db.load)

    def test_caller_changes_do_not_leak(self):
        naive_member_price_view(self.cache.get("lamp"), MEMBER_DISCOUNT_PERCENT)
        naive_public_view(self.cache.get("lamp"))
        self.cache.get("lamp")["tags"].append("sale")
        fresh = self.cache.get("lamp")
        self.assertEqual(fresh["price_cents"], LAMP_PRICE_CENTS)
        self.assertEqual(fresh["cost_cents"], 900)
        self.assertEqual(fresh["tags"], ["lighting", "office"])

    def test_each_caller_gets_its_own_object_but_one_load(self):
        self.assertIsNot(self.cache.get("lamp"), self.cache.get("lamp"))
        self.assertEqual(self.db.loads, 1)


class FreezeHelpers(unittest.TestCase):
    def test_freeze_then_thaw_round_trips(self):
        original = {"a": [1, {"b": [2, 3]}], "c": {"d"}, "e": None}
        self.assertEqual(thaw(freeze(original)), original)

    def test_discount_percent_out_of_range_is_rejected(self):
        product = freeze({"price_cents": 100})
        for bad_percent in (-1, 101):
            with self.assertRaises(ValueError):
                member_price_view(product, bad_percent)


if __name__ == "__main__":
    unittest.main()
