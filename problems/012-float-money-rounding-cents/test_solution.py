import random
import unittest
from decimal import Decimal

from solution import (ROUND_HALF_EVEN, Line, allocate, build_invoice, format_cents,
                      installments, naive_installments, naive_split_by_weight,
                      naive_tax, tax_cents, to_cents)


class ReproduceFloatMoney(unittest.TestCase):
    def test_half_cent_tax_rounds_the_wrong_way(self):
        # 6.00 * 8.25% = 0.495 exactly; half-up should give 0.50.
        self.assertEqual(naive_tax(6.00, 0.0825), 0.49)
        self.assertEqual(naive_tax(1.25, 0.18), 0.22)   # exact 0.225 -> 0.23
        self.assertEqual(round(2.675, 2), 2.67)

    def test_installments_lose_a_cent(self):
        parts = naive_installments(100.00, 3)
        self.assertEqual(parts, [33.33, 33.33, 33.33])
        self.assertAlmostEqual(sum(parts), 99.99)

    def test_independent_rounding_breaks_sums_often(self):
        rng = random.Random(7)
        bad = 0
        for _ in range(10_000):
            total = rng.randint(100, 100_000) / 100
            n = rng.choice([2, 3, 4, 6, 12])
            if round(sum(naive_installments(total, n)), 2) != total:
                bad += 1
        self.assertGreater(bad / 10_000, 0.70)        # 72% of plans don't add up

    def test_float_sum_is_not_exact(self):
        self.assertNotEqual(0.1 + 0.2, 0.3)


class ExactMoney(unittest.TestCase):
    def test_tax_half_up(self):
        self.assertEqual(tax_cents(600, "0.0825"), 50)
        self.assertEqual(tax_cents(125, "0.18"), 23)
        self.assertEqual(tax_cents(5000, "0.0825"), 413)

    def test_tax_banker_rounding_option(self):
        self.assertEqual(tax_cents(125, "0.18", ROUND_HALF_EVEN), 22)   # 22.5 -> 22
        self.assertEqual(tax_cents(5000, "0.0825", ROUND_HALF_EVEN), 412)  # 412.5 -> 412

    def test_installments_sum_exactly(self):
        self.assertEqual(installments(10_000, 3), [3334, 3333, 3333])
        rng = random.Random(7)
        for _ in range(10_000):
            total = rng.randint(100, 100_000)
            n = rng.choice([2, 3, 4, 6, 12])
            parts = installments(total, n)
            self.assertEqual(sum(parts), total)
            self.assertLessEqual(max(parts) - min(parts), 1)

    def test_weighted_split_sums_exactly(self):
        rng = random.Random(11)
        for _ in range(5_000):
            total = rng.randint(1, 100_000)
            weights = [rng.randint(0, 10) for _ in range(rng.randint(1, 6))]
            if sum(weights) == 0:
                continue
            parts = allocate(total, weights)
            self.assertEqual(sum(parts), total)
            for p, w in zip(parts, weights):
                exact = Decimal(total) * w / sum(weights)
                self.assertLess(abs(p - exact), 1)   # every part within a cent of exact
                if w == 0:
                    self.assertEqual(p, 0)

    def test_naive_weighted_split_can_disagree(self):
        self.assertAlmostEqual(sum(naive_split_by_weight(10.00, [1, 1, 1])), 9.99)
        self.assertEqual(sum(allocate(1000, [1, 1, 1])), 1000)

    def test_refund_split_keeps_sign(self):
        self.assertEqual(allocate(-1000, [1, 1, 1]), [-334, -333, -333])

    def test_fewer_cents_than_parts(self):
        self.assertEqual(allocate(5, [1] * 7), [1, 1, 1, 1, 1, 0, 0])
        self.assertEqual(allocate(0, [1, 2]), [0, 0])

    def test_invalid_inputs(self):
        with self.assertRaises(TypeError):
            to_cents(19.99)
        with self.assertRaises(ValueError):
            to_cents("1.005")
        with self.assertRaises(ValueError):
            allocate(100, [0, 0])
        with self.assertRaises(ValueError):
            allocate(100, [])
        with self.assertRaises(ValueError):
            installments(100, 0)

    def test_parse_and_format(self):
        self.assertEqual(to_cents("19.99"), 1999)
        self.assertEqual(to_cents(Decimal("0.10")), 10)
        self.assertEqual(to_cents("-3.5"), -350)
        self.assertEqual(format_cents(1999), "19.99")
        self.assertEqual(format_cents(-5), "-0.05")

    def test_invoice_totals_are_consistent(self):
        inv = build_invoice([Line("pizza", "19.99", 3), Line("napkins", "0.10", 10)], "0.0825")
        self.assertEqual(inv.lines, [5997, 100])
        self.assertEqual(inv.subtotal, 6097)
        self.assertEqual(inv.tax, 503)
        self.assertEqual(inv.total, sum(inv.lines) + inv.tax)
        self.assertEqual(format_cents(inv.total), "66.00")


if __name__ == "__main__":
    unittest.main()
