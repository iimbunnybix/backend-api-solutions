import unittest

from solution import DEFAULT_LIMIT, BadRequest, naive_search, parse_filters, search


def make_orders() -> list[dict[str, str]]:
    """Two pending, two failed and one paid order across two customers."""
    rows = [("o1", "pending", "c1"), ("o2", "failed", "c1"), ("o3", "paid", "c2"),
            ("o4", "pending", "c2"), ("o5", "failed", "c2")]
    return [{"id": oid, "status": status, "customer_id": cid} for oid, status, cid in rows]


def ids(orders: list[dict[str, str]]) -> list[str]:
    return [order["id"] for order in orders]


class ReproduceProblem(unittest.TestCase):
    def test_pending_or_failed_returns_only_failed_orders(self):
        result = naive_search(make_orders(), "status=pending&status=failed")
        self.assertEqual(ids(result), ["o2", "o5"])

    def test_swapping_the_order_of_params_changes_the_result(self):
        result = naive_search(make_orders(), "status=failed&status=pending")
        self.assertEqual(ids(result), ["o1", "o4"])

    def test_misspelled_filter_silently_returns_everything(self):
        self.assertEqual(len(naive_search(make_orders(), "stauts=paid")), 5)


class FixKeepsEveryValue(unittest.TestCase):
    def test_repeated_status_means_any_of_them(self):
        result = search(make_orders(), "status=pending&status=failed")
        self.assertEqual(ids(result), ["o1", "o2", "o4", "o5"])

    def test_param_order_does_not_matter(self):
        first = search(make_orders(), "status=pending&status=failed")
        second = search(make_orders(), "status=failed&status=pending")
        self.assertEqual(first, second)

    def test_status_and_customer_filters_combine(self):
        result = search(make_orders(), "status=pending&status=failed&customer_id=c2")
        self.assertEqual(ids(result), ["o4", "o5"])

    def test_no_status_means_any_status(self):
        self.assertEqual(len(search(make_orders(), "")), 5)

    def test_limit_caps_the_page(self):
        self.assertEqual(ids(search(make_orders(), "limit=2")), ["o1", "o2"])

    def test_default_limit_is_applied(self):
        self.assertEqual(parse_filters("").limit, DEFAULT_LIMIT)

    def test_encoded_values_are_decoded(self):
        self.assertEqual(parse_filters("customer_id=c%201").customer_id, "c 1")


class FixRejectsAmbiguousQueries(unittest.TestCase):
    def test_single_valued_param_given_twice_is_rejected(self):
        with self.assertRaises(BadRequest):
            parse_filters("customer_id=c1&customer_id=c2")

    def test_unknown_parameter_is_rejected(self):
        with self.assertRaises(BadRequest):
            parse_filters("stauts=paid")

    def test_unknown_status_value_is_rejected(self):
        with self.assertRaises(BadRequest):
            parse_filters("status=shipped")

    def test_blank_status_is_rejected_not_ignored(self):
        with self.assertRaises(BadRequest):
            parse_filters("status=")

    def test_limit_out_of_range_is_rejected(self):
        for raw in ("0", "101", "-5", "abc", "²"):
            with self.subTest(raw=raw), self.assertRaises(BadRequest):
                parse_filters(f"limit={raw}")


if __name__ == "__main__":
    unittest.main()
