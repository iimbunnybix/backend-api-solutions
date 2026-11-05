import sqlite3
import unittest

from solution import (
    DuplicateAccount,
    canonical_identifier,
    create_naive_schema,
    create_schema,
    find_user_id,
    naive_sign_up,
    sign_up,
)

FULL_WIDTH_ALICE = "ａｌｉｃｅ"           # "ａｌｉｃｅ" from some phone keyboards
COMPOSED_JOSE = "josé"                                   # é as one code point
DECOMPOSED_JOSE = "josé"                                # e + combining acute accent


def naive_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    create_naive_schema(conn)
    return conn


def fixed_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    create_schema(conn)
    return conn


class ReproduceProblem(unittest.TestCase):
    def test_different_case_creates_second_account(self):
        conn = naive_db()
        naive_sign_up(conn, "Alice", "alice@example.com")
        naive_sign_up(conn, "alice", "Alice@Example.com")
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM users").fetchone()[0], 2)

    def test_full_width_letters_create_lookalike_account(self):
        conn = naive_db()
        naive_sign_up(conn, "alice", "a@example.com")
        naive_sign_up(conn, FULL_WIDTH_ALICE, "b@example.com")
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM users").fetchone()[0], 2)

    def test_composed_and_decomposed_accents_are_different_accounts(self):
        conn = naive_db()
        naive_sign_up(conn, COMPOSED_JOSE, "a@example.com")
        naive_sign_up(conn, DECOMPOSED_JOSE, "b@example.com")
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM users").fetchone()[0], 2)


class FixCanonicalKey(unittest.TestCase):
    def test_case_variants_share_one_key(self):
        self.assertEqual(canonical_identifier("ALICE"), canonical_identifier("alice"))

    def test_full_width_letters_map_to_ascii(self):
        self.assertEqual(canonical_identifier(FULL_WIDTH_ALICE), "alice")

    def test_accent_forms_share_one_key(self):
        self.assertEqual(canonical_identifier(COMPOSED_JOSE), canonical_identifier(DECOMPOSED_JOSE))

    def test_sharp_s_folds_to_ss(self):
        self.assertEqual(canonical_identifier("Straße"), canonical_identifier("STRASSE"))

    def test_surrounding_whitespace_is_ignored(self):
        self.assertEqual(canonical_identifier("  bob "), "bob")


class FixSignUp(unittest.TestCase):
    def test_case_variant_username_is_rejected(self):
        conn = fixed_db()
        sign_up(conn, "Alice", "alice@example.com")
        with self.assertRaises(DuplicateAccount):
            sign_up(conn, "alice", "other@example.com")

    def test_case_variant_email_is_rejected(self):
        conn = fixed_db()
        sign_up(conn, "alice", "alice@example.com")
        with self.assertRaises(DuplicateAccount):
            sign_up(conn, "alice2", "ALICE@Example.COM")

    def test_full_width_lookalike_is_rejected(self):
        conn = fixed_db()
        sign_up(conn, "alice", "a@example.com")
        with self.assertRaises(DuplicateAccount):
            sign_up(conn, FULL_WIDTH_ALICE, "b@example.com")

    def test_display_name_keeps_what_the_user_typed(self):
        conn = fixed_db()
        sign_up(conn, "Alice", "Alice@Example.com")
        row = conn.execute("SELECT username_display, email_display FROM users").fetchone()
        self.assertEqual(row, ("Alice", "Alice@Example.com"))

    def test_login_finds_user_by_any_spelling(self):
        conn = fixed_db()
        user_id = sign_up(conn, "Alice", "alice@example.com")
        self.assertEqual(find_user_id(conn, FULL_WIDTH_ALICE.upper()), user_id)

    def test_unknown_user_is_not_found(self):
        self.assertIsNone(find_user_id(fixed_db(), "nobody"))

    def test_zero_width_space_username_is_rejected(self):
        with self.assertRaises(ValueError):
            sign_up(fixed_db(), "ad​min", "a@example.com")

    def test_empty_username_is_rejected(self):
        with self.assertRaises(ValueError):
            sign_up(fixed_db(), "   ", "a@example.com")

    def test_over_long_username_is_rejected(self):
        with self.assertRaises(ValueError):
            sign_up(fixed_db(), "a" * 33, "a@example.com")

    def test_distinct_usernames_are_both_accepted(self):
        conn = fixed_db()
        sign_up(conn, "alice", "a@example.com")
        sign_up(conn, "alicia", "b@example.com")
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM users").fetchone()[0], 2)


if __name__ == "__main__":
    unittest.main()
