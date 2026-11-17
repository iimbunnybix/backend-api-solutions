import unittest

from solution import LineItem, naive_split_discount, refund_for_items, split_discount

COUPON_100_RUPEES = 10_000  # paise


def equal_items(count: int, price_paise: int = 49_900) -> list[LineItem]:
    """count identical items priced 499 rupees each."""
    return [LineItem(f"sku-{number}", price_paise) for number in range(count)]


def amount_paid(discount_paise: int, items: list[LineItem]) -> int:
    return sum(item.price_paise for item in items) - discount_paise


class ReproduceProblem(unittest.TestCase):
    def test_three_items_lose_one_paisa_of_discount(self):
        shares = naive_split_discount(COUPON_100_RUPEES, equal_items(3))
        self.assertEqual(sum(shares.values()), 9_999)

    def test_six_items_hand_out_more_discount_than_the_coupon(self):
        shares = naive_split_discount(COUPON_100_RUPEES, equal_items(6))
        self.assertEqual(sum(shares.values()), 10_002)

    def test_returning_every_item_refunds_more_than_the_customer_paid(self):
        items = equal_items(3)
        shares = naive_split_discount(COUPON_100_RUPEES, items)
        refund = refund_for_items([item.sku for item in items], items, shares)
        self.assertEqual(refund - amount_paid(COUPON_100_RUPEES, items), 1)


class FixSharesAddUpExactly(unittest.TestCase):
    def test_three_equal_items_sum_to_the_coupon(self):
        shares = split_discount(COUPON_100_RUPEES, equal_items(3))
        self.assertEqual(sum(shares.values()), COUPON_100_RUPEES)
        self.assertEqual(sorted(shares.values()), [3_333, 3_333, 3_334])

    def test_full_return_refunds_exactly_what_was_paid(self):
        items = equal_items(6)
        shares = split_discount(COUPON_100_RUPEES, items)
        refund = refund_for_items([item.sku for item in items], items, shares)
        self.assertEqual(refund, amount_paid(COUPON_100_RUPEES, items))

    def test_every_order_size_from_1_to_50_sums_exactly(self):
        for count in range(1, 51):
            shares = split_discount(COUPON_100_RUPEES, equal_items(count))
            self.assertEqual(sum(shares.values()), COUPON_100_RUPEES, count)

    def test_shares_stay_proportional_to_price(self):
        items = [LineItem("phone", 1_999_900), LineItem("case", 49_900), LineItem("cable", 29_900)]
        shares = split_discount(COUPON_100_RUPEES, items)
        self.assertEqual(shares, {"phone": 9_616, "case": 240, "cable": 144})

    def test_each_share_is_within_one_paisa_of_the_exact_value(self):
        items = [LineItem("a", 333), LineItem("b", 667), LineItem("c", 1_001)]
        shares = split_discount(1_000, items)
        for item in items:
            exact = 1_000 * item.price_paise / 2_001
            self.assertLess(abs(shares[item.sku] - exact), 1)

    def test_ties_go_to_the_earlier_line_so_invoices_are_reproducible(self):
        first = split_discount(COUPON_100_RUPEES, equal_items(3))
        second = split_discount(COUPON_100_RUPEES, equal_items(3))
        self.assertEqual(first, second)
        self.assertEqual(first["sku-0"], 3_334)

    def test_free_item_gets_no_discount(self):
        items = [LineItem("paid", 50_000), LineItem("gift", 0)]
        self.assertEqual(split_discount(COUPON_100_RUPEES, items), {"paid": 10_000, "gift": 0})

    def test_zero_discount_gives_zero_shares(self):
        self.assertEqual(set(split_discount(0, equal_items(4)).values()), {0})


class FixRejectsBadInput(unittest.TestCase):
    def test_empty_order_is_rejected(self):
        with self.assertRaises(ValueError):
            split_discount(COUPON_100_RUPEES, [])

    def test_discount_larger_than_order_is_rejected(self):
        with self.assertRaises(ValueError):
            split_discount(COUPON_100_RUPEES, [LineItem("pen", 5_000)])

    def test_negative_discount_is_rejected(self):
        with self.assertRaises(ValueError):
            split_discount(-1, equal_items(2))

    def test_duplicate_sku_is_rejected(self):
        with self.assertRaises(ValueError):
            split_discount(100, [LineItem("x", 500), LineItem("x", 500)])


if __name__ == "__main__":
    unittest.main()
