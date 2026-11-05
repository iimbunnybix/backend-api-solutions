import unittest

from solution import (HTTP_BAD_REQUEST, HTTP_MULTI_STATUS, HTTP_OK, HTTP_PAYLOAD_TOO_LARGE,
                      MAX_BATCH_SIZE, ProductDatabase, bulk_import, make_rows, naive_import)

UPLOAD_SIZE = 1_000
BAD_ROW = 812


class ReproduceProblem(unittest.TestCase):
    def test_one_bad_row_rolls_back_every_good_row(self):
        database = ProductDatabase()
        status, body = naive_import(database, make_rows(UPLOAD_SIZE, {BAD_ROW}))
        self.assertEqual(status, HTTP_BAD_REQUEST)
        self.assertEqual(len(database.products), 0)

    def test_error_reports_only_the_first_bad_row(self):
        database = ProductDatabase()
        _, body = naive_import(database, make_rows(UPLOAD_SIZE, {10, 20, 30}))
        self.assertIn("row 10", body["error"])
        self.assertNotIn("20", body["error"])


class FixWithPerRowResults(unittest.TestCase):
    def test_good_rows_are_saved_when_one_row_is_bad(self):
        database = ProductDatabase()
        status, body = bulk_import(database, make_rows(UPLOAD_SIZE, {BAD_ROW}))
        self.assertEqual(status, HTTP_MULTI_STATUS)
        self.assertEqual(len(database.products), UPLOAD_SIZE - 1)
        self.assertEqual(body["counts"]["invalid"], 1)

    def test_every_bad_row_is_reported_with_its_index(self):
        database = ProductDatabase()
        _, body = bulk_import(database, make_rows(50, {10, 20, 30}))
        invalid = [row["index"] for row in body["results"] if row["status"] == "invalid"]
        self.assertEqual(invalid, [10, 20, 30])

    def test_a_row_lists_all_of_its_errors(self):
        _, body = bulk_import(ProductDatabase(), [{"sku": "", "name": "", "price_cents": -1}])
        self.assertEqual(len(body["results"][0]["errors"]), 3)

    def test_duplicate_sku_in_the_same_upload_is_reported_not_fatal(self):
        rows = [{"sku": "A", "name": "One", "price_cents": 100},
                {"sku": "A", "name": "Two", "price_cents": 200},
                {"sku": "B", "name": "Three", "price_cents": 300}]
        database = ProductDatabase()
        _, body = bulk_import(database, rows)
        self.assertEqual([row["status"] for row in body["results"]],
                         ["created", "duplicate", "created"])
        self.assertEqual(database.products["A"]["name"], "One")

    def test_boolean_price_is_rejected(self):
        _, body = bulk_import(ProductDatabase(), [{"sku": "A", "name": "x", "price_cents": True}])
        self.assertEqual(body["results"][0]["status"], "invalid")

    def test_clean_upload_returns_200(self):
        status, body = bulk_import(ProductDatabase(), make_rows(10, set()))
        self.assertEqual(status, HTTP_OK)
        self.assertEqual(body["counts"]["created"], 10)

    def test_retrying_the_same_upload_marks_rows_as_duplicates(self):
        database = ProductDatabase()
        bulk_import(database, make_rows(5, set()))
        _, body = bulk_import(database, make_rows(5, set()))
        self.assertEqual(body["counts"]["duplicate"], 5)
        self.assertEqual(len(database.products), 5)

    def test_oversized_batch_is_rejected_before_any_write(self):
        database = ProductDatabase()
        status, _ = bulk_import(database, make_rows(MAX_BATCH_SIZE + 1, set()))
        self.assertEqual(status, HTTP_PAYLOAD_TOO_LARGE)
        self.assertEqual(len(database.products), 0)

    def test_batch_at_exact_limit_is_accepted(self):
        status, _ = bulk_import(ProductDatabase(), make_rows(MAX_BATCH_SIZE, set()))
        self.assertEqual(status, HTTP_OK)

    def test_empty_upload_succeeds_with_zero_counts(self):
        status, body = bulk_import(ProductDatabase(), [])
        self.assertEqual(status, HTTP_OK)
        self.assertEqual(body["results"], [])


if __name__ == "__main__":
    unittest.main()
