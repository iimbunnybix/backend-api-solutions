import sqlite3
import unittest

from solution import (HTTP_CREATED, HTTP_INTERNAL_ERROR, HTTP_OK, count_users_with_email,
                      create_users_table, double_tap_login, find_user, get_or_create,
                      handle_login, naive_get_or_create, open_database)

EMAIL = "asha@example.com"
NAME = "Asha"
SIGNUPS = 100


def make_database(unique_email: bool = True) -> sqlite3.Connection:
    """Fresh users table, with the unique index unless told otherwise."""
    connection = open_database()
    create_users_table(connection, unique_email=unique_email)
    return connection


def signup_emails(count: int) -> list[str]:
    """Distinct new-user emails for a burst of first logins."""
    return [f"user{index}@example.com" for index in range(count)]


class ReproduceProblem(unittest.TestCase):
    def test_double_tap_creates_two_accounts_without_unique_index(self):
        connection = make_database(unique_email=False)
        statuses = double_tap_login(connection, EMAIL, NAME, naive_get_or_create)
        self.assertEqual(statuses, [HTTP_CREATED, HTTP_CREATED])
        self.assertEqual(count_users_with_email(connection, EMAIL), 2)

    def test_double_tap_returns_500_once_the_unique_index_exists(self):
        connection = make_database(unique_email=True)
        statuses = double_tap_login(connection, EMAIL, NAME, naive_get_or_create)
        self.assertEqual(statuses, [HTTP_CREATED, HTTP_INTERNAL_ERROR])

    def test_every_double_tapped_first_login_fails_once(self):
        connection = make_database(unique_email=True)
        errors = 0
        for email in signup_emails(SIGNUPS):
            errors += double_tap_login(connection, email, NAME, naive_get_or_create).count(
                HTTP_INTERNAL_ERROR)
        self.assertEqual(errors, SIGNUPS)

    def test_naive_version_is_fine_without_concurrency(self):
        connection = make_database()
        self.assertEqual(handle_login(connection, EMAIL, NAME, naive_get_or_create), HTTP_CREATED)
        self.assertEqual(handle_login(connection, EMAIL, NAME, naive_get_or_create), HTTP_OK)


class FixInsertOnConflict(unittest.TestCase):
    def test_double_tap_succeeds_for_both_requests(self):
        connection = make_database()
        statuses = double_tap_login(connection, EMAIL, NAME, get_or_create)
        self.assertEqual(statuses, [HTTP_CREATED, HTTP_OK])

    def test_double_tap_leaves_exactly_one_account(self):
        connection = make_database()
        double_tap_login(connection, EMAIL, NAME, get_or_create)
        self.assertEqual(count_users_with_email(connection, EMAIL), 1)

    def test_loser_returns_the_winners_row(self):
        connection = make_database()
        results = []

        def other_device_signs_up_first():
            results.append(get_or_create(connection, EMAIL, "Asha (tablet)"))

        user, created = get_or_create(connection, EMAIL, "Asha (phone)", other_device_signs_up_first)
        winner, winner_created = results[0]
        self.assertTrue(winner_created)
        self.assertFalse(created)
        self.assertEqual(user, winner)
        self.assertEqual(user.name, "Asha (tablet)")

    def test_burst_of_double_taps_has_no_errors_and_no_duplicates(self):
        connection = make_database()
        statuses = []
        for email in signup_emails(SIGNUPS):
            statuses.extend(double_tap_login(connection, email, NAME, get_or_create))
        self.assertEqual(statuses.count(HTTP_CREATED), SIGNUPS)
        self.assertEqual(statuses.count(HTTP_OK), SIGNUPS)
        total_rows = connection.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        self.assertEqual(total_rows, SIGNUPS)

    def test_returning_user_is_found_without_a_write(self):
        connection = make_database()
        first, _ = get_or_create(connection, EMAIL, NAME)
        changes_before = connection.total_changes
        again, created = get_or_create(connection, EMAIL, "Different Name")
        self.assertFalse(created)
        self.assertEqual(again, first)
        self.assertEqual(connection.total_changes, changes_before)

    def test_different_emails_get_different_accounts(self):
        connection = make_database()
        first, _ = get_or_create(connection, "a@example.com", NAME)
        second, _ = get_or_create(connection, "b@example.com", NAME)
        self.assertNotEqual(first.id, second.id)

    def test_fix_needs_the_unique_index(self):
        connection = make_database(unique_email=False)
        # Without a unique constraint SQLite has nothing to conflict on, so
        # ON CONFLICT (email) is rejected instead of silently allowing dupes.
        with self.assertRaises(sqlite3.OperationalError):
            get_or_create(connection, EMAIL, NAME)

    def test_find_user_returns_none_for_unknown_email(self):
        self.assertIsNone(find_user(make_database(), "nobody@example.com"))


if __name__ == "__main__":
    unittest.main()
