import unittest

from solution import (MAX_PAGE_SIZE, InvalidPageRequest, export_by_has_next,
                      export_by_page_count, list_orders, naive_total_pages, total_pages)


def orders(count: int) -> list[int]:
    """Order ids 1..count, oldest first."""
    return list(range(1, count + 1))


class ReproduceProblem(unittest.TestCase):
    def test_floor_division_drops_the_partial_last_page(self):
        self.assertEqual(naive_total_pages(1_234, 100), 12)

    def test_export_by_page_count_misses_the_newest_34_orders(self):
        exported = export_by_page_count(orders(1_234), 100, naive_total_pages)
        self.assertEqual(len(exported), 1_200)
        self.assertNotIn(1_234, exported)

    def test_customer_with_fewer_orders_than_a_page_gets_an_empty_export(self):
        self.assertEqual(export_by_page_count(orders(60), 100, naive_total_pages), [])


class FixRoundUpPageCount(unittest.TestCase):
    def test_partial_last_page_is_counted(self):
        self.assertEqual(total_pages(1_234, 100), 13)

    def test_exact_multiple_does_not_add_an_empty_page(self):
        self.assertEqual(total_pages(1_200, 100), 12)

    def test_no_orders_means_zero_pages(self):
        self.assertEqual(total_pages(0, 100), 0)

    def test_single_order_needs_one_page(self):
        self.assertEqual(total_pages(1, 100), 1)

    def test_huge_counts_stay_exact(self):
        # math.ceil((2**53 + 1) / 2) gives 2**52, one page short, because
        # the float division rounds 2**53 + 1 down to 2**53.
        self.assertEqual(total_pages(2**53 + 1, 2), 2**52 + 1)

    def test_zero_page_size_is_rejected(self):
        with self.assertRaises(InvalidPageRequest):
            total_pages(10, 0)

    def test_export_by_page_count_gets_every_order(self):
        self.assertEqual(export_by_page_count(orders(1_234), 100), orders(1_234))

    def test_small_customer_export_is_complete(self):
        self.assertEqual(export_by_page_count(orders(60), 100), orders(60))


class FixHasNext(unittest.TestCase):
    def test_last_page_reports_no_next_page(self):
        response = list_orders(orders(1_234), page=13, page_size=100)
        self.assertEqual(len(response.items), 34)
        self.assertFalse(response.has_next)

    def test_full_last_page_reports_no_next_page(self):
        self.assertFalse(list_orders(orders(200), page=2, page_size=100).has_next)

    def test_earlier_page_reports_a_next_page(self):
        self.assertTrue(list_orders(orders(1_234), page=12, page_size=100).has_next)

    def test_has_next_loop_exports_every_order(self):
        self.assertEqual(export_by_has_next(orders(1_234), 100), orders(1_234))

    def test_page_zero_is_rejected(self):
        with self.assertRaises(InvalidPageRequest):
            list_orders(orders(10), page=0)

    def test_page_size_above_the_maximum_is_rejected(self):
        with self.assertRaises(InvalidPageRequest):
            list_orders(orders(10), page=1, page_size=MAX_PAGE_SIZE + 1)


if __name__ == "__main__":
    unittest.main()
