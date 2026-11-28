import unittest

from solution import (LineItem, build_quote, find_shared_mutable_defaults,
                      original_build_quote)


def bulk_order(sku: str) -> list[LineItem]:
    """An order that triggers one quantity warning."""
    return [LineItem(sku, unit_price_cents=250, quantity=500)]


def clean_order() -> list[LineItem]:
    return [LineItem("PEN-BLUE", unit_price_cents=120, quantity=3)]


class ReproduceProblem(unittest.TestCase):
    def test_another_customers_warning_shows_up_in_a_clean_order(self):
        original_build_quote.__defaults__[0].clear()   # start from a fresh process
        original_build_quote(bulk_order("ACME-CRATE"))
        quote = original_build_quote(clean_order())
        self.assertEqual(quote.warnings, ["ACME-CRATE: quantity 500 capped at 100"])

    def test_warnings_keep_growing_with_every_request(self):
        original_build_quote.__defaults__[0].clear()
        for request_number in range(5):
            quote = original_build_quote(bulk_order(f"SKU-{request_number}"))
        self.assertEqual(len(quote.warnings), 5)

    def test_the_original_handler_has_a_shared_default(self):
        self.assertEqual(find_shared_mutable_defaults(original_build_quote), ["warnings"])


class FixFreshListPerCall(unittest.TestCase):
    def test_clean_order_has_no_warnings_after_a_bulk_order(self):
        build_quote(bulk_order("ACME-CRATE"))
        self.assertEqual(build_quote(clean_order()).warnings, [])

    def test_each_request_sees_only_its_own_warning(self):
        for request_number in range(5):
            quote = build_quote(bulk_order(f"SKU-{request_number}"))
        self.assertEqual(quote.warnings, ["SKU-4: quantity 500 capped at 100"])

    def test_total_caps_quantity_at_the_line_limit(self):
        self.assertEqual(build_quote(bulk_order("ACME-CRATE")).total_cents, 250 * 100)

    def test_free_item_gets_a_price_warning(self):
        quote = build_quote([LineItem("SAMPLE", unit_price_cents=0, quantity=1)])
        self.assertEqual(quote.warnings, ["SAMPLE: free item, check the price list"])

    def test_caller_list_is_not_mutated(self):
        caller_warnings = ["coupon expired"]
        quote = build_quote(bulk_order("ACME-CRATE"), caller_warnings)
        self.assertEqual(caller_warnings, ["coupon expired"])
        self.assertEqual(len(quote.warnings), 2)

    def test_changing_a_returned_quote_does_not_affect_the_next_one(self):
        first = build_quote(clean_order())
        first.warnings.append("edited by a later middleware")
        self.assertEqual(build_quote(clean_order()).warnings, [])

    def test_empty_order_totals_zero(self):
        self.assertEqual(build_quote([]).total_cents, 0)

    def test_fixed_handler_has_no_shared_defaults(self):
        self.assertEqual(find_shared_mutable_defaults(build_quote), [])


if __name__ == "__main__":
    unittest.main()
