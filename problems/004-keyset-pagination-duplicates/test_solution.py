import os
import sqlite3
import unittest

from solution import CursorCodec, InvalidCursor, create_schema, page_keyset, page_offset


def make_db(n=100, same_ts_every=1):
    conn = sqlite3.connect(":memory:")
    create_schema(conn)
    conn.executemany("INSERT INTO events VALUES (?, ?, ?)",
                     [(i, (i // same_ts_every) * 10, f"e{i}") for i in range(1, n + 1)])
    return conn


def insert_new(conn, k):
    top = conn.execute("SELECT MAX(id), MAX(created_at) FROM events").fetchone()
    conn.executemany("INSERT INTO events VALUES (?, ?, ?)",
                     [(top[0] + j, top[1] + j, "new") for j in range(1, k + 1)])


class PaginationTests(unittest.TestCase):
    def setUp(self):
        self.codec = CursorCodec(os.urandom(32))

    def test_offset_duplicates_rows_when_new_rows_arrive(self):
        conn = make_db()
        p0 = page_offset(conn, 0, 20)
        insert_new(conn, 5)                       # 5 new events while user reads page 0
        p1 = page_offset(conn, 1, 20)
        dup = {r[0] for r in p0} & {r[0] for r in p1}
        self.assertEqual(len(dup), 5)             # reproduces the bug

    def test_offset_skips_rows_when_rows_deleted(self):
        conn = make_db()
        p0 = page_offset(conn, 0, 20)
        conn.execute("DELETE FROM events WHERE id IN (100, 99, 98)")
        p1 = page_offset(conn, 1, 20)
        seen = {r[0] for r in p0} | {r[0] for r in p1}
        expected = set(range(60, 98)) | {98, 99, 100}
        self.assertEqual(len(expected - seen), 3)  # 3 live rows never shown

    def test_keyset_no_duplicates_with_concurrent_inserts(self):
        conn = make_db()
        seen, cursor = [], None
        while True:
            page = page_keyset(conn, self.codec, 20, cursor)
            seen += [r[0] for r in page.rows]
            insert_new(conn, 3)                   # writes during every page
            if page.next_cursor is None:
                break
            cursor = page.next_cursor
        self.assertEqual(seen, list(range(100, 0, -1)))

    def test_keyset_ties_on_timestamp(self):
        conn = make_db(n=95, same_ts_every=7)     # 7 rows share each timestamp
        seen, cursor = [], None
        while True:
            page = page_keyset(conn, self.codec, 4, cursor)
            seen += [r[0] for r in page.rows]
            if not page.next_cursor:
                break
            cursor = page.next_cursor
        self.assertEqual(sorted(seen), list(range(1, 96)))
        self.assertEqual(len(seen), len(set(seen)))

    def test_exact_multiple_has_no_empty_trailing_page(self):
        conn = make_db(n=40)
        p1 = page_keyset(conn, self.codec, 20)
        p2 = page_keyset(conn, self.codec, 20, p1.next_cursor)
        self.assertEqual(len(p2.rows), 20)
        self.assertIsNone(p2.next_cursor)

    def test_empty_table(self):
        page = page_keyset(make_db(n=0), self.codec, 10)
        self.assertEqual(page.rows, [])
        self.assertIsNone(page.next_cursor)

    def test_tampered_cursor_rejected(self):
        conn = make_db()
        cur = page_keyset(conn, self.codec, 10).next_cursor
        payload, sig = cur.rsplit(".", 1)
        forged = self.codec.encode(10**12, 10**9).rsplit(".", 1)[0] + "." + sig
        for bad in (forged, "garbage", payload + ".", ""):
            with self.assertRaises(InvalidCursor):
                page_keyset(conn, self.codec, 10, bad)

    def test_non_ascii_cursor_is_rejected_as_invalid_not_crash(self):
        conn = make_db()
        for bad in ("abc.\u00e9", "\u00e9\u00e9.\u00e9"):
            with self.assertRaises(InvalidCursor):
                page_keyset(conn, self.codec, 10, bad)

    def test_cursor_from_other_key_rejected(self):
        conn = make_db()
        cur = CursorCodec(os.urandom(32)).encode(500, 50)
        with self.assertRaises(InvalidCursor):
            page_keyset(conn, self.codec, 10, cur)

    def test_page_size_bounds(self):
        conn = make_db()
        for bad in (0, 101):
            with self.assertRaises(ValueError):
                page_keyset(conn, self.codec, bad)

    def test_query_uses_index(self):
        conn = make_db()
        plan = " ".join(str(r) for r in conn.execute(
            "EXPLAIN QUERY PLAN SELECT id FROM events WHERE (created_at, id) < (50, 5) "
            "ORDER BY created_at DESC, id DESC LIMIT 10"))
        self.assertIn("idx_events_feed", plan)


if __name__ == "__main__":
    unittest.main()
