import unittest
from decimal import Decimal

from solution import (
    AmountFormatError,
    client_accepts,
    format_amount,
    format_unit_price,
    naive_format_amount,
)


class ReproduceProblem(unittest.TestCase):
    def test_round_balance_is_sent_in_exponent_notation(self):
        self.assertEqual(naive_format_amount(Decimal("10.00")), "1E+1")

    def test_hundred_dollars_becomes_one_e_plus_two(self):
        self.assertEqual(naive_format_amount(Decimal("100.00")), "1E+2")

    def test_tiny_unit_price_is_sent_in_exponent_notation(self):
        self.assertEqual(naive_format_amount(Decimal("0.00000015")), "1.5E-7")

    def test_client_rejects_the_exponent_strings(self):
        self.assertFalse(client_accepts(naive_format_amount(Decimal("10.00"))))
        self.assertFalse(client_accepts(naive_format_amount(Decimal("0.00000015"))))

    def test_balance_with_cents_happened_to_work(self):
        # Why the bug slipped through review: most test balances had cents.
        self.assertEqual(naive_format_amount(Decimal("10.55")), "10.55")


class FixFixedPointAmounts(unittest.TestCase):
    def test_round_balance_keeps_its_cents(self):
        self.assertEqual(format_amount(Decimal("10"), "USD"), "10.00")

    def test_normalized_input_is_still_written_in_full(self):
        self.assertEqual(format_amount(Decimal("1E+2"), "USD"), "100.00")

    def test_large_balance_has_no_exponent(self):
        self.assertEqual(format_amount(Decimal("1E+9"), "EUR"), "1000000000.00")

    def test_yen_has_no_decimal_places(self):
        self.assertEqual(format_amount(Decimal("1500"), "JPY"), "1500")

    def test_kuwaiti_dinar_has_three_decimal_places(self):
        self.assertEqual(format_amount(Decimal("2.5"), "KWD"), "2.500")

    def test_negative_amount_keeps_its_sign(self):
        self.assertEqual(format_amount(Decimal("-0.5"), "USD"), "-0.50")

    def test_negative_zero_is_sent_as_plain_zero(self):
        self.assertEqual(format_amount(Decimal("-0.00"), "USD"), "0.00")

    def test_every_formatted_amount_passes_the_client_validator(self):
        samples = ["10", "1E+1", "0", "-0", "123456789.1", "0.01", "-42"]
        for sample in samples:
            with self.subTest(sample=sample):
                self.assertTrue(client_accepts(format_amount(Decimal(sample), "USD")))


class FixRefusesSilentRounding(unittest.TestCase):
    def test_fraction_of_a_cent_is_refused_not_rounded(self):
        with self.assertRaises(AmountFormatError):
            format_amount(Decimal("10.005"), "USD")

    def test_trailing_zeros_beyond_the_currency_places_are_fine(self):
        self.assertEqual(format_amount(Decimal("10.5000"), "USD"), "10.50")

    def test_fractional_yen_is_refused(self):
        with self.assertRaises(AmountFormatError):
            format_amount(Decimal("1.5"), "JPY")

    def test_unknown_currency_is_refused(self):
        with self.assertRaises(AmountFormatError):
            format_amount(Decimal("1"), "XYZ")

    def test_nan_and_infinity_are_refused(self):
        for value in ("NaN", "Infinity", "-Infinity"):
            with self.subTest(value=value):
                with self.assertRaises(AmountFormatError):
                    format_amount(Decimal(value), "USD")


class FixUnitPrices(unittest.TestCase):
    def test_tiny_unit_price_is_written_in_full(self):
        self.assertEqual(format_unit_price(Decimal("1.5E-7")), "0.00000015")

    def test_unit_price_passes_the_client_validator(self):
        self.assertTrue(client_accepts(format_unit_price(Decimal("0.00000015"))))

    def test_unit_price_with_too_many_places_is_refused(self):
        with self.assertRaises(AmountFormatError):
            format_unit_price(Decimal("0.000000001"))


if __name__ == "__main__":
    unittest.main()
