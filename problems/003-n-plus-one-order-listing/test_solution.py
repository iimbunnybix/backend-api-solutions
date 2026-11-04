import random
import sqlite3
import unittest

from solution import QueryCounter, create_schema, list_orders_batched, list_orders_naive


def seeded_db(n_customers=40, n_orders=300, seed=7):
    rng = random.Random(seed)
    conn = sqlite3.connect(":memory:")
    create_schema(conn)
    conn.executemany("INSERT INTO customers VALUES (?, ?)",
                     [(i, f"customer-{i}") for i in range(1, n_customers + 1)])
    item_id = 1
    for oid in range(1, n_orders + 1):
        conn.execute("INSERT INTO orders VALUES (?, ?, ?, ?)",
                     (oid, rng.randint(1, n_customers), rng.randint(100, 50_000), oid * 10))
        for _ in range(rng.randint(0, 4)):          # some orders have no items
            conn.execute("INSERT INTO order_items VALUES (?, ?, ?, ?)",
                         (item_id, oid, f"SKU-{rng.randint(1, 99)}", rng.randint(1, 3)))
            item_id += 1
    conn.commit()
    return conn


class NPlusOneTests(unittest.TestCase):
    def setUp(self):
        self.conn = seeded_db()

    def test_naive_issues_2n_plus_1_queries(self):
        db = QueryCounter(self.conn)
        list_orders_naive(db, limit=50)
        self.assertEqual(db.count, 101)              # reproduces the incident

    def test_batched_uses_three_queries(self):
        db = QueryCounter(self.conn)
        list_orders_batched(db, limit=50)
        self.assertEqual(db.count, 3)

    def test_query_count_independent_of_page_size(self):
        for limit in (1, 10, 200):
            db = QueryCounter(self.conn)
            list_orders_batched(db, limit=limit)
            self.assertEqual(db.count, 3, limit)

    def test_same_response_as_naive(self):
        for limit in (1, 50, 300):
            a = list_orders_naive(QueryCounter(self.conn), limit)
            b = list_orders_batched(QueryCounter(self.conn), limit)
            self.assertEqual(a, b)

    def test_orders_without_items_get_empty_list(self):
        rows = list_orders_batched(QueryCounter(self.conn), limit=300)
        empty = [r for r in rows if not r["items"]]
        self.assertTrue(empty)
        self.assertTrue(all(r["items"] == [] for r in empty))

    def test_empty_table(self):
        conn = sqlite3.connect(":memory:")
        create_schema(conn)
        db = QueryCounter(conn)
        self.assertEqual(list_orders_batched(db), [])
        self.assertEqual(db.count, 1)

    def test_large_page_is_chunked_to_bounded_in_lists(self):
        db = QueryCounter(self.conn)
        rows = list_orders_batched(db, limit=300, chunk=100)
        # 1 page + customers (40 distinct ids -> 1 chunk) + items (300 ids -> 3 chunks)
        self.assertEqual(db.count, 5)
        self.assertEqual(rows, list_orders_naive(QueryCounter(self.conn), 300))

    def test_page_order_preserved(self):
        rows = list_orders_batched(QueryCounter(self.conn), limit=20)
        self.assertEqual([r["id"] for r in rows], list(range(300, 280, -1)))


if __name__ == "__main__":
    unittest.main()
