import unittest
from decimal import Decimal

from solution import (
    DE_FORMAT,
    EN_FORMAT,
    PriceFormatError,
    import_prices,
    naive_parse_price,
    parse_price,
)


class ReproduceProblem(unittest.TestCase):
    def test_german_twelve_fifty_becomes_one_thousand_two_hundred_fifty(self):
        self.assertEqual(naive_parse_price("12,50"), 1250.0)

    def test_german_thousands_price_becomes_about_one_euro(self):
        self.assertAlmostEqual(naive_parse_price("1.234,56"), 1.23456)

    def test_english_format_happened_to_work(self):
        self.assertEqual(naive_parse_price("1,234.56"), 1234.56)


class FixGermanFormat(unittest.TestCase):
    def test_decimal_comma_is_read_as_cents(self):
        self.assertEqual(parse_price("12,50", DE_FORMAT), Decimal("12.50"))

    def test_dot_groups_thousands(self):
        self.assertEqual(parse_price("1.234,56", DE_FORMAT), Decimal("1234.56"))

    def test_whole_number_without_separators(self):
        self.assertEqual(parse_price("12", DE_FORMAT), Decimal("12"))

    def test_english_style_value_is_rejected(self):
        with self.assertRaises(PriceFormatError):
            parse_price("1,234.56", DE_FORMAT)


class FixEnglishFormat(unittest.TestCase):
    def test_comma_groups_thousands(self):
        self.assertEqual(parse_price("1,234.56", EN_FORMAT), Decimal("1234.56"))

    def test_german_style_value_is_rejected_not_multiplied(self):
        with self.assertRaises(PriceFormatError):
            parse_price("12,50", EN_FORMAT)

    def test_group_of_wrong_size_is_rejected(self):
        for text in ("1,23", "1,2345", "12,34,567"):
            with self.subTest(text=text), self.assertRaises(PriceFormatError):
                parse_price(text, EN_FORMAT)


class FixRejectsUnreadableValues(unittest.TestCase):
    def test_bad_values_are_rejected(self):
        for text in ("", "  ", "-5", "1e3", "12.345", "12.", ".5", "١٢", "nan"):
            with self.subTest(text=text), self.assertRaises(PriceFormatError):
                parse_price(text, EN_FORMAT)

    def test_surrounding_whitespace_is_ignored(self):
        self.assertEqual(parse_price(" 7.25 ", EN_FORMAT), Decimal("7.25"))

    def test_result_is_exact_decimal_not_float(self):
        self.assertIsInstance(parse_price("0,10", DE_FORMAT), Decimal)
        self.assertEqual(str(parse_price("0,10", DE_FORMAT)), "0.10")


class FixImportReportsBadRows(unittest.TestCase):
    def test_good_rows_import_and_bad_rows_are_reported(self):
        rows = [("SKU-1", "12,50"), ("SKU-2", "1.234,56"), ("SKU-3", "12.50")]
        result = import_prices(rows, DE_FORMAT)
        self.assertEqual(result.prices, {"SKU-1": Decimal("12.50"), "SKU-2": Decimal("1234.56")})
        self.assertEqual(list(result.errors), ["SKU-3"])
        self.assertIn("1.234,50", result.errors["SKU-3"])


if __name__ == "__main__":
    unittest.main()
