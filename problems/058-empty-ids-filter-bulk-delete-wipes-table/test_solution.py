import sqlite3
import unittest

from solution import (
    DELETE_ALL_CONFIRMATION,
    MAX_IDS_PER_REQUEST,
    BadRequestError,
    build_delete_by_ids,
    create_schema,
    delete_all_notes,
    delete_notes,
    naive_delete_notes,
)

ALICE = 1
BOB = 2


def make_database() -> sqlite3.Connection:
    """Alice owns notes 1-3, Bob owns notes 4-5."""
    connection = sqlite3.connect(":memory:")
    create_schema(connection)
    rows = [(1, ALICE), (2, ALICE), (3, ALICE), (4, BOB), (5, BOB)]
    with connection:
        connection.executemany("INSERT INTO notes (id, owner_id, body) VALUES (?, ?, 'x')", rows)
    return connection


def remaining_ids(connection: sqlite3.Connection) -> list[int]:
    return [row[0] for row in connection.execute("SELECT id FROM notes ORDER BY id")]


class ReproduceProblem(unittest.TestCase):
    def test_listed_ids_are_deleted_as_expected(self):
        database = make_database()
        naive_delete_notes(database, "1,2")
        self.assertEqual(remaining_ids(database), [3, 4, 5])

    def test_empty_ids_param_deletes_every_users_notes(self):
        database = make_database()
        deleted = naive_delete_notes(database, "")
        self.assertEqual(deleted, 5)
        self.assertEqual(remaining_ids(database), [])

    def test_lone_comma_also_deletes_everything(self):
        database = make_database()
        naive_delete_notes(database, ",")
        self.assertEqual(remaining_ids(database), [])

    def test_ids_are_not_scoped_to_the_caller(self):
        database = make_database()
        # Nothing stops a caller from deleting another user's note by id.
        naive_delete_notes(database, "4")
        self.assertNotIn(4, remaining_ids(database))


class FixEmptyFilterIsRejected(unittest.TestCase):
    def test_empty_ids_param_is_a_bad_request(self):
        database = make_database()
        with self.assertRaises(BadRequestError):
            delete_notes(database, ALICE, "")
        self.assertEqual(remaining_ids(database), [1, 2, 3, 4, 5])

    def test_lone_comma_is_a_bad_request(self):
        database = make_database()
        with self.assertRaises(BadRequestError):
            delete_notes(database, ALICE, ",")
        self.assertEqual(len(remaining_ids(database)), 5)

    def test_stray_comma_between_ids_is_a_bad_request(self):
        with self.assertRaises(BadRequestError):
            delete_notes(make_database(), ALICE, "1,,2")

    def test_non_numeric_id_is_a_bad_request(self):
        with self.assertRaises(BadRequestError):
            delete_notes(make_database(), ALICE, "1,abc")

    def test_negative_id_is_a_bad_request(self):
        with self.assertRaises(BadRequestError):
            delete_notes(make_database(), ALICE, "-1")

    def test_too_many_ids_is_a_bad_request(self):
        too_many = ",".join(str(index) for index in range(MAX_IDS_PER_REQUEST + 1))
        with self.assertRaises(BadRequestError):
            delete_notes(make_database(), ALICE, too_many)

    def test_query_builder_refuses_an_empty_list(self):
        with self.assertRaises(BadRequestError):
            build_delete_by_ids(ALICE, [])


class FixDeleteIsScopedToTheCaller(unittest.TestCase):
    def test_listed_ids_are_deleted(self):
        database = make_database()
        self.assertEqual(delete_notes(database, ALICE, "1, 2"), 2)
        self.assertEqual(remaining_ids(database), [3, 4, 5])

    def test_duplicate_ids_are_counted_once(self):
        database = make_database()
        self.assertEqual(delete_notes(database, ALICE, "1,1,1"), 1)

    def test_other_users_note_is_not_deleted(self):
        database = make_database()
        self.assertEqual(delete_notes(database, ALICE, "4"), 0)
        self.assertIn(4, remaining_ids(database))

    def test_missing_ids_delete_nothing(self):
        database = make_database()
        self.assertEqual(delete_notes(database, ALICE, "99"), 0)

    def test_generated_sql_always_has_a_where_clause(self):
        sql, params = build_delete_by_ids(ALICE, [3])
        self.assertIn("WHERE owner_id = ?", sql)
        self.assertEqual(params, [ALICE, 3])


class FixDeleteAllNeedsConfirmation(unittest.TestCase):
    def test_delete_all_without_confirmation_is_refused(self):
        database = make_database()
        with self.assertRaises(BadRequestError):
            delete_all_notes(database, ALICE, "")
        self.assertEqual(len(remaining_ids(database)), 5)

    def test_confirmed_delete_all_removes_only_the_callers_notes(self):
        database = make_database()
        self.assertEqual(delete_all_notes(database, ALICE, DELETE_ALL_CONFIRMATION), 3)
        self.assertEqual(remaining_ids(database), [4, 5])


if __name__ == "__main__":
    unittest.main()
