import unittest

from solution import MAX_PREVIEW_BYTES, build_preview, naive_truncate, truncate_utf8

HINDI_MESSAGE = "Priya: नमस्ते, आपका ऑर्डर तैयार है"
FAMILY_EMOJI = "\U0001F468‍\U0001F469‍\U0001F467"   # man ZWJ woman ZWJ girl
THUMBS_UP_MEDIUM_SKIN = "\U0001F44D\U0001F3FD"
E_WITH_COMBINING_ACUTE = "é"


class ReproduceProblem(unittest.TestCase):
    def test_cut_inside_a_hindi_letter_raises_decode_error(self):
        with self.assertRaises(UnicodeDecodeError):
            naive_truncate(HINDI_MESSAGE, 41)

    def test_cut_inside_an_emoji_raises_decode_error(self):
        with self.assertRaises(UnicodeDecodeError):
            naive_truncate("Hi " + FAMILY_EMOJI, 5)

    def test_cut_on_a_boundary_can_still_drop_the_skin_tone(self):
        self.assertEqual(naive_truncate("Hi " + THUMBS_UP_MEDIUM_SKIN, 7), "Hi \U0001F44D")


class FixTruncatesOnSymbolBoundaries(unittest.TestCase):
    def test_result_never_exceeds_the_byte_limit(self):
        for limit in range(len(HINDI_MESSAGE.encode("utf-8")) + 1):
            self.assertLessEqual(len(truncate_utf8(HINDI_MESSAGE, limit).encode("utf-8")), limit)

    def test_every_limit_gives_a_prefix_of_the_original(self):
        for limit in range(len(HINDI_MESSAGE.encode("utf-8")) + 1):
            self.assertTrue(HINDI_MESSAGE.startswith(truncate_utf8(HINDI_MESSAGE, limit)))

    def test_hindi_vowel_sign_is_not_separated_from_its_consonant(self):
        # Cutting after "त" would show the consonant without its "े" sign.
        self.assertEqual(truncate_utf8("Priya: नमस्ते", 22), "Priya: नमस्")

    def test_family_emoji_is_kept_whole_or_dropped(self):
        text = "Hi " + FAMILY_EMOJI
        for limit in range(len(text.encode("utf-8"))):
            self.assertEqual(truncate_utf8(text, limit)[:3], text[:min(3, limit)])
            self.assertNotIn("\U0001F468", truncate_utf8(text, limit))
        self.assertEqual(truncate_utf8(text, len(text.encode("utf-8"))), text)

    def test_skin_tone_is_kept_with_its_emoji(self):
        self.assertEqual(truncate_utf8("Hi " + THUMBS_UP_MEDIUM_SKIN, 7), "Hi ")

    def test_combining_accent_stays_with_its_letter(self):
        self.assertEqual(truncate_utf8("Jos" + E_WITH_COMBINING_ACUTE, 4), "Jos")

    def test_short_text_is_returned_unchanged(self):
        self.assertEqual(truncate_utf8("ok", 10), "ok")

    def test_zero_limit_gives_empty_string(self):
        self.assertEqual(truncate_utf8(HINDI_MESSAGE, 0), "")

    def test_negative_limit_is_rejected(self):
        with self.assertRaises(ValueError):
            truncate_utf8("ok", -1)

    def test_preview_fits_the_gateway_limit(self):
        preview = build_preview("Priya", "नमस्ते, आपका ऑर्डर तैयार है")
        self.assertLessEqual(len(preview.encode("utf-8")), MAX_PREVIEW_BYTES)
        self.assertTrue(preview.startswith("Priya: नमस्ते"))


if __name__ == "__main__":
    unittest.main()
