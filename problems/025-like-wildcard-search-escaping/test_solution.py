import unittest

from solution import (
    MAX_RESULTS,
    SearchTermTooShort,
    create_customer_db,
    escape_like,
    naive_search,
    safe_search,
)

EMAILS = [
    "ann@example.com",
    "bob_smith@example.com",
    "bobXsmith@example.com",
    "promo50%off@example.com",
    "promo50xoff@example.com",
    "back\\slash@example.com",
]


def make_db():
    return create_customer_db(EMAILS)


class ReproduceProblem(unittest.TestCase):
    def test_underscore_search_matches_every_customer(self):
        self.assertEqual(naive_search(make_db(), "_"), EMAILS)

    def test_percent_search_matches_every_customer(self):
        self.assertEqual(naive_search(make_db(), "%"), EMAILS)

    def test_underscore_in_name_also_matches_other_characters(self):
        found = naive_search(make_db(), "bob_smith")
        self.assertEqual(found, ["bob_smith@example.com", "bobXsmith@example.com"])


class FixEscapesWildcards(unittest.TestCase):
    def test_underscore_is_matched_literally(self):
        self.assertEqual(safe_search(make_db(), "bob_smith"), ["bob_smith@example.com"])

    def test_percent_is_matched_literally(self):
        self.assertEqual(safe_search(make_db(), "50%off"), ["promo50%off@example.com"])

    def test_two_wildcards_match_nothing_when_no_email_contains_them(self):
        self.assertEqual(safe_search(make_db(), "%%"), [])

    def test_backslash_is_matched_literally(self):
        self.assertEqual(safe_search(make_db(), "k\\s"), ["back\\slash@example.com"])

    def test_plain_search_still_works(self):
        self.assertEqual(safe_search(make_db(), "ann@"), ["ann@example.com"])

    def test_escape_like_escapes_backslash_before_metacharacters(self):
        self.assertEqual(escape_like("a\\%_"), "a\\\\\\%\\_")


class FixRejectsTooShortTerms(unittest.TestCase):
    def test_single_character_search_is_rejected(self):
        with self.assertRaises(SearchTermTooShort):
            safe_search(make_db(), "a")

    def test_whitespace_only_search_is_rejected(self):
        with self.assertRaises(SearchTermTooShort):
            safe_search(make_db(), "   ")

    def test_results_are_capped(self):
        db = create_customer_db([f"user{index}@example.com" for index in range(200)])
        self.assertEqual(len(safe_search(db, "@example")), MAX_RESULTS)


if __name__ == "__main__":
    unittest.main()
