import unittest

from solution import BadPathError, RouteMatch, naive_route, route

SLASH_SKU_PATH = "/v1/products/TOOLS%2FDRILL-18V"


class ReproduceProblem(unittest.TestCase):
    def test_encoded_slash_sku_goes_to_the_store_stock_route(self):
        self.assertEqual(
            naive_route(SLASH_SKU_PATH),
            RouteMatch("get_product_stock_in_store", {"sku": "TOOLS", "store_id": "DRILL-18V"}),
        )

    def test_sku_with_two_slashes_matches_no_route(self):
        self.assertIsNone(naive_route("/v1/products/A%2FB%2FC"))

    def test_plain_sku_happened_to_work(self):
        self.assertEqual(naive_route("/v1/products/KETTLE-1"), RouteMatch("get_product", {"sku": "KETTLE-1"}))


class FixDecodesPerSegment(unittest.TestCase):
    def test_encoded_slash_stays_inside_the_sku(self):
        self.assertEqual(route(SLASH_SKU_PATH), RouteMatch("get_product", {"sku": "TOOLS/DRILL-18V"}))

    def test_sku_with_two_slashes_is_one_parameter(self):
        self.assertEqual(route("/v1/products/A%2FB%2FC"), RouteMatch("get_product", {"sku": "A/B/C"}))

    def test_lowercase_hex_escape_is_accepted(self):
        self.assertEqual(route("/v1/products/A%2fB").params, {"sku": "A/B"})

    def test_real_slash_still_separates_segments(self):
        self.assertEqual(
            route("/v1/products/TOOLS%2FDRILL-18V/store-7"),
            RouteMatch("get_product_stock_in_store", {"sku": "TOOLS/DRILL-18V", "store_id": "store-7"}),
        )

    def test_double_encoded_slash_is_decoded_only_once(self):
        self.assertEqual(route("/v1/products/A%252FB").params, {"sku": "A%2FB"})

    def test_utf8_sku_is_decoded(self):
        self.assertEqual(route("/v1/products/CAF%C3%89-01").params, {"sku": "CAFÉ-01"})

    def test_unknown_path_matches_no_route(self):
        self.assertIsNone(route("/v1/orders/1"))


class FixRejectsBadEncoding(unittest.TestCase):
    def test_percent_without_hex_digits_is_rejected(self):
        with self.assertRaises(BadPathError):
            route("/v1/products/100%")

    def test_percent_followed_by_non_hex_is_rejected(self):
        with self.assertRaises(BadPathError):
            route("/v1/products/A%ZZ")

    def test_invalid_utf8_bytes_are_rejected(self):
        with self.assertRaises(BadPathError):
            route("/v1/products/A%FF")


if __name__ == "__main__":
    unittest.main()
