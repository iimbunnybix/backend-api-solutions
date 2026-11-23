import unittest

from solution import (DEFAULT_PAGE_SIZE, HTTP_BAD_REQUEST, HTTP_OK, MAX_PAGE_SIZE, OrderStore,
                      list_orders, naive_list_orders)

ORDER_COUNT = 100_000


def new_store(order_count: int = ORDER_COUNT) -> OrderStore:
    return OrderStore(order_count)


class ReproduceProblem(unittest.TestCase):
    def test_huge_limit_reads_the_whole_table(self):
        store = new_store()
        response = naive_list_orders({"limit": "500000"}, store)
        self.assertEqual(len(response.body["items"]), ORDER_COUNT)
        self.assertEqual(store.rows_read, ORDER_COUNT)

    def test_negative_limit_returns_all_rows_but_one(self):
        response = naive_list_orders({"limit": "-1"}, new_store())
        self.assertEqual(len(response.body["items"]), ORDER_COUNT - 1)

    def test_non_numeric_limit_crashes_the_handler(self):
        with self.assertRaises(ValueError):
            naive_list_orders({"limit": "all"}, new_store())


class FixBoundedPageSize(unittest.TestCase):
    def test_huge_limit_is_clamped_to_the_maximum(self):
        store = new_store()
        response = list_orders({"limit": "500000"}, store)
        self.assertEqual(response.status, HTTP_OK)
        self.assertEqual(len(response.body["items"]), MAX_PAGE_SIZE)
        self.assertEqual(store.rows_read, MAX_PAGE_SIZE)

    def test_applied_limit_is_echoed_back(self):
        response = list_orders({"limit": "500000"}, new_store())
        self.assertEqual(response.body["limit"], MAX_PAGE_SIZE)

    def test_missing_limit_uses_the_default(self):
        response = list_orders({}, new_store())
        self.assertEqual(len(response.body["items"]), DEFAULT_PAGE_SIZE)

    def test_negative_limit_is_rejected(self):
        response = list_orders({"limit": "-1"}, new_store())
        self.assertEqual(response.status, HTTP_BAD_REQUEST)

    def test_zero_limit_is_rejected(self):
        response = list_orders({"limit": "0"}, new_store())
        self.assertEqual(response.status, HTTP_BAD_REQUEST)

    def test_fractional_and_text_limits_are_rejected(self):
        for raw_limit in ["1.5", "all", "", " 10", "１０"]:
            with self.subTest(raw_limit=raw_limit):
                response = list_orders({"limit": raw_limit}, new_store())
                self.assertEqual(response.status, HTTP_BAD_REQUEST)

    def test_negative_offset_is_rejected(self):
        response = list_orders({"offset": "-5"}, new_store())
        self.assertEqual(response.status, HTTP_BAD_REQUEST)


class FixPagingThroughEverything(unittest.TestCase):
    def test_following_next_offset_returns_every_order_once(self):
        store = new_store(order_count=1_050)
        seen: list[int] = []
        query = {"limit": "500000"}
        while True:
            response = list_orders(query, store)
            seen.extend(response.body["items"])
            if response.body["next_offset"] is None:
                break
            query = {"limit": "500000", "offset": str(response.body["next_offset"])}
        self.assertEqual(seen, list(range(1, 1_051)))

    def test_short_last_page_has_no_next_offset(self):
        response = list_orders({"offset": "99990"}, new_store())
        self.assertEqual(len(response.body["items"]), 10)
        self.assertIsNone(response.body["next_offset"])

    def test_exactly_full_last_page_ends_with_an_empty_page(self):
        store = new_store(order_count=MAX_PAGE_SIZE)
        first = list_orders({"limit": str(MAX_PAGE_SIZE)}, store)
        second = list_orders({"offset": str(first.body["next_offset"])}, store)
        self.assertEqual(second.body["items"], [])
        self.assertIsNone(second.body["next_offset"])


if __name__ == "__main__":
    unittest.main()
