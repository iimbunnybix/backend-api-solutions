import sqlite3
import unittest

from solution import (LEGACY_SQLITE_PARAMETER_LIMIT, MAX_IDS_PER_REQUEST, TooManyIds,
                      chunks, create_customer_table, load_customers, naive_load_customers,
                      queries_needed)

TENANT = 1
OTHER_TENANT = 2


class CountingConnection:
    """Wraps a sqlite3 connection and counts executed statements."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection
        self.statements = 0

    def execute(self, sql, parameters=()):
        self.statements += 1
        return self.connection.execute(sql, parameters)

    def getlimit(self, category):
        return self.connection.getlimit(category)


def make_database(customer_count: int = 5000) -> sqlite3.Connection:
    """In-memory database configured like the legacy production build."""
    connection = sqlite3.connect(":memory:")
    connection.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, LEGACY_SQLITE_PARAMETER_LIMIT)
    create_customer_table(connection, customer_count, tenant_id=TENANT)
    return connection


class ReproduceProblem(unittest.TestCase):
    def test_small_selection_works_with_the_naive_query(self):
        rows = naive_load_customers(make_database(), TENANT, [1, 2, 3])
        self.assertEqual(len(rows), 3)

    def test_selecting_one_thousand_customers_crashes_the_naive_query(self):
        with self.assertRaises(sqlite3.OperationalError) as caught:
            naive_load_customers(make_database(), TENANT, list(range(1, 1001)))
        self.assertIn("too many SQL variables", str(caught.exception))

    def test_naive_query_fails_one_id_earlier_than_the_limit_because_of_tenant_param(self):
        with self.assertRaises(sqlite3.OperationalError):
            naive_load_customers(make_database(), TENANT, list(range(1, 1000)))


class FixChunkedLookup(unittest.TestCase):
    def test_one_thousand_customers_load_successfully(self):
        rows = load_customers(make_database(), TENANT, list(range(1, 1001)))
        self.assertEqual(len(rows), 1000)

    def test_five_thousand_ids_use_six_queries(self):
        connection = CountingConnection(make_database())
        load_customers(connection, TENANT, list(range(1, 5001)))
        self.assertEqual(connection.statements, 6)
        self.assertEqual(queries_needed(5000, LEGACY_SQLITE_PARAMETER_LIMIT), 6)

    def test_results_keep_the_callers_order_across_chunks(self):
        requested = list(range(3000, 0, -7))
        rows = load_customers(make_database(), TENANT, requested)
        self.assertEqual([customer_id for customer_id, _ in rows], requested)

    def test_duplicate_ids_are_returned_once(self):
        rows = load_customers(make_database(), TENANT, [5, 5, 9, 5])
        self.assertEqual([customer_id for customer_id, _ in rows], [5, 9])

    def test_missing_ids_are_skipped(self):
        rows = load_customers(make_database(customer_count=10), TENANT, [1, 999_999, 2])
        self.assertEqual([customer_id for customer_id, _ in rows], [1, 2])

    def test_tenant_filter_still_applies_in_every_chunk(self):
        rows = load_customers(make_database(), OTHER_TENANT, list(range(1, 2001)))
        self.assertEqual(rows, [])

    def test_empty_selection_runs_no_query(self):
        connection = CountingConnection(make_database(customer_count=1))
        self.assertEqual(load_customers(connection, TENANT, []), [])
        self.assertEqual(connection.statements, 0)

    def test_requests_above_the_api_cap_are_rejected(self):
        with self.assertRaises(TooManyIds):
            load_customers(make_database(customer_count=1), TENANT,
                           list(range(MAX_IDS_PER_REQUEST + 1)))


class ChunkHelper(unittest.TestCase):
    def test_chunks_cover_every_item_without_overlap(self):
        self.assertEqual(list(chunks([1, 2, 3, 4, 5], 2)), [[1, 2], [3, 4], [5]])

    def test_chunk_size_below_one_is_rejected(self):
        with self.assertRaises(ValueError):
            list(chunks([1], 0))


if __name__ == "__main__":
    unittest.main()
