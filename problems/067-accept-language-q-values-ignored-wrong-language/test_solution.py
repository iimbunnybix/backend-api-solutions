import unittest

from solution import naive_pick_language, parse_accept_language, pick_language, ranked_tags


class ReproduceProblem(unittest.TestCase):
    def test_low_quality_first_tag_is_served(self):
        # The user prefers German; French is only a last resort.
        self.assertEqual(naive_pick_language("fr;q=0.1, de"), "fr")

    def test_refused_language_with_q_zero_is_served(self):
        self.assertEqual(naive_pick_language("fr;q=0, de"), "fr")

    def test_regional_tag_falls_back_to_english(self):
        self.assertEqual(naive_pick_language("de-CH, en;q=0.5"), "en")


class FixRanksByQuality(unittest.TestCase):
    def test_highest_quality_language_wins(self):
        self.assertEqual(pick_language("fr;q=0.1, de"), "de")

    def test_equal_quality_keeps_header_order(self):
        self.assertEqual(pick_language("fr;q=0.8, de;q=0.8"), "fr")

    def test_missing_q_means_one(self):
        self.assertEqual(pick_language("de;q=0.9, fr"), "fr")

    def test_unsupported_top_choice_falls_to_next_supported(self):
        self.assertEqual(pick_language("ja, fr;q=0.7, en;q=0.3"), "fr")

    def test_ranked_tags_order(self):
        self.assertEqual(ranked_tags("en;q=0.5, de-CH, fr;q=0.9"), ["de-ch", "fr", "en"])


class FixRefusalsAndFallbacks(unittest.TestCase):
    def test_q_zero_language_is_never_served(self):
        self.assertEqual(pick_language("fr;q=0, de;q=0.2"), "de")

    def test_regional_tag_matches_primary_language(self):
        self.assertEqual(pick_language("de-CH, en;q=0.5"), "de")

    def test_exact_regional_match_is_case_insensitive(self):
        self.assertEqual(pick_language("PT-br"), "pt-BR")

    def test_wildcard_gives_default(self):
        self.assertEqual(pick_language("ja, *;q=0.5"), "en")

    def test_wildcard_skips_refused_default(self):
        self.assertEqual(pick_language("en;q=0, *;q=0.5, fr;q=0.1"), "fr")

    def test_missing_or_empty_header_gives_default(self):
        self.assertEqual(pick_language(None), "en")
        self.assertEqual(pick_language(""), "en")

    def test_nothing_supported_gives_default(self):
        self.assertEqual(pick_language("ja, ko;q=0.5"), "en")


class FixIgnoresMalformedEntries(unittest.TestCase):
    def test_invalid_q_values_are_skipped(self):
        for bad in ("fr;q=abc, de;q=0.5", "fr;q=2, de;q=0.5", "fr;q=-1, de;q=0.5",
                    "fr;q=0.12345, de;q=0.5", "fr;level=1, de;q=0.5",
                    "fr;level=1;q=0.9, de;q=0.5"):
            with self.subTest(header=bad):
                self.assertEqual(pick_language(bad), "de")

    def test_empty_entries_are_ignored(self):
        self.assertEqual([p.tag for p in parse_accept_language(" , fr,, de ")], ["fr", "de"])

    def test_whitespace_around_q_is_allowed(self):
        self.assertEqual(pick_language("fr ; q=0.2 , de ; q=0.9"), "de")


if __name__ == "__main__":
    unittest.main()
