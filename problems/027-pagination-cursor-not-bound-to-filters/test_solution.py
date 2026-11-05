import base64
import json
import unittest

from solution import (
    SORT_LARGEST_TOTAL,
    SORT_NEWEST,
    CursorCodec,
    CursorQueryMismatch,
    InvalidCursor,
    Order,
    list_orders,
    naive_list_orders,
    query_fingerprint,
)

PAGE_SIZE = 3


def make_orders():
    """Ten orders: newer ids are newer; odd ids shipped; totals 500..5000 cents."""
    orders = []
    for order_id in range(1, 11):
        status = "shipped" if order_id % 2 else "pending"
        orders.append(Order(order_id, status, 1000 + order_id * 10, order_id * 500))
    return orders


def ids(page):
    return [order.id for order in page.orders]


def make_codec():
    return CursorCodec()


def read_all(orders, codec, status, sort):
    """Follow cursors until the end and return every id seen."""
    seen, cursor = [], None
    while True:
        page = list_orders(orders, codec, status, sort, cursor, PAGE_SIZE)
        seen.extend(ids(page))
        cursor = page.next_cursor
        if cursor is None:
            return seen


class ReproduceProblem(unittest.TestCase):
    def test_changing_status_filter_mid_scroll_skips_newer_matches(self):
        orders = make_orders()
        first = naive_list_orders(orders, None, SORT_NEWEST, None, PAGE_SIZE)
        self.assertEqual(ids(first), [10, 9, 8])
        # UI switches to "shipped" but keeps the cursor from the unfiltered list.
        second = naive_list_orders(orders, "shipped", SORT_NEWEST, first.next_cursor, PAGE_SIZE)
        self.assertEqual(ids(second), [7, 5, 3])   # shipped order 9 was skipped

    def test_changing_sort_mid_scroll_returns_wrong_rows(self):
        orders = make_orders()
        first = naive_list_orders(orders, None, SORT_LARGEST_TOTAL, None, PAGE_SIZE)
        self.assertEqual(ids(first), [10, 9, 8])
        # The cursor now holds a total (4000 cents) but is compared to created_at.
        second = naive_list_orders(orders, None, SORT_NEWEST, first.next_cursor, PAGE_SIZE)
        self.assertEqual(ids(second), [10, 9, 8])  # repeats instead of continuing


class FixBindsCursorToQuery(unittest.TestCase):
    def setUp(self):
        self.orders = make_orders()
        self.codec = make_codec()

    def test_paging_with_same_query_returns_every_row_once(self):
        self.assertEqual(read_all(self.orders, self.codec, None, SORT_NEWEST),
                         [10, 9, 8, 7, 6, 5, 4, 3, 2, 1])

    def test_paging_filtered_query_returns_every_match_once(self):
        self.assertEqual(read_all(self.orders, self.codec, "shipped", SORT_LARGEST_TOTAL),
                         [9, 7, 5, 3, 1])

    def test_cursor_reused_with_other_status_is_rejected(self):
        first = list_orders(self.orders, self.codec, None, SORT_NEWEST, None, PAGE_SIZE)
        with self.assertRaises(CursorQueryMismatch):
            list_orders(self.orders, self.codec, "shipped", SORT_NEWEST,
                        first.next_cursor, PAGE_SIZE)

    def test_cursor_reused_with_other_sort_is_rejected(self):
        first = list_orders(self.orders, self.codec, None, SORT_LARGEST_TOTAL, None, PAGE_SIZE)
        with self.assertRaises(CursorQueryMismatch):
            list_orders(self.orders, self.codec, None, SORT_NEWEST,
                        first.next_cursor, PAGE_SIZE)

    def test_mismatch_error_tells_client_to_restart(self):
        first = list_orders(self.orders, self.codec, None, SORT_NEWEST, None, PAGE_SIZE)
        with self.assertRaises(CursorQueryMismatch) as context:
            list_orders(self.orders, self.codec, "pending", SORT_NEWEST,
                        first.next_cursor, PAGE_SIZE)
        self.assertIn("first page", str(context.exception))

    def test_changing_page_size_keeps_cursor_valid(self):
        first = list_orders(self.orders, self.codec, None, SORT_NEWEST, None, PAGE_SIZE)
        second = list_orders(self.orders, self.codec, None, SORT_NEWEST, first.next_cursor, 5)
        self.assertEqual(ids(second), [7, 6, 5, 4, 3])

    def test_unknown_sort_is_rejected(self):
        with self.assertRaises(ValueError):
            list_orders(self.orders, self.codec, None, "cheapest", None, PAGE_SIZE)


class FixRejectsForgedCursors(unittest.TestCase):
    def setUp(self):
        self.orders = make_orders()
        self.codec = make_codec()

    def forge(self, fingerprint, position):
        """Build a cursor with a valid body but no valid signature."""
        body = json.dumps({"fp": fingerprint, "pos": position}).encode()
        return base64.urlsafe_b64encode(body).decode() + "." + "0" * 32

    def test_client_cannot_rewrite_fingerprint(self):
        forged = self.forge(query_fingerprint("shipped", SORT_NEWEST), [1080, 8])
        with self.assertRaises(InvalidCursor) as context:
            list_orders(self.orders, self.codec, "shipped", SORT_NEWEST, forged, PAGE_SIZE)
        self.assertNotIsInstance(context.exception, CursorQueryMismatch)

    def test_cursor_from_another_key_is_rejected(self):
        other_codec = make_codec()
        first = list_orders(self.orders, other_codec, None, SORT_NEWEST, None, PAGE_SIZE)
        with self.assertRaises(InvalidCursor):
            list_orders(self.orders, self.codec, None, SORT_NEWEST, first.next_cursor, PAGE_SIZE)

    def test_garbage_and_non_ascii_cursors_are_rejected(self):
        for cursor in ("", "no-dot", ".abc", "abc.def", "é.é"):
            with self.subTest(cursor=cursor):
                with self.assertRaises(InvalidCursor):
                    list_orders(self.orders, self.codec, None, SORT_NEWEST, cursor, PAGE_SIZE)

    def test_signed_cursor_with_wrong_shape_is_rejected(self):
        key = b"k" * 32
        codec = CursorCodec(key)
        payload = base64.urlsafe_b64encode(b'{"fp":"x","pos":["a",1]}').decode()
        cursor = f"{payload}.{codec._sign(payload)}"
        with self.assertRaises(InvalidCursor):
            codec.decode(cursor, "x")


if __name__ == "__main__":
    unittest.main()
