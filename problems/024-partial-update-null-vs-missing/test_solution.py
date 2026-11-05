import json
import unittest

from solution import (
    MISSING,
    PatchValidationError,
    apply_patch,
    original_patch_profile,
    parse_patch,
    skip_none_patch_profile,
)


def stored_profile() -> dict:
    return {"user_id": 42, "display_name": "Asha", "phone": "+91-98000-00000",
            "bio": "Runner, reader."}


def request_body(raw_json: str) -> dict:
    return json.loads(raw_json)


class ReproduceProblem(unittest.TestCase):
    def test_renaming_wipes_phone_and_bio(self):
        updated = original_patch_profile(stored_profile(), request_body('{"display_name": "Ann"}'))
        self.assertEqual(updated["display_name"], "Ann")
        self.assertIsNone(updated["phone"])
        self.assertIsNone(updated["bio"])

    def test_empty_body_wipes_everything_including_required_name(self):
        updated = original_patch_profile(stored_profile(), {})
        self.assertIsNone(updated["display_name"])

    def test_skip_none_hotfix_cannot_clear_phone(self):
        updated = skip_none_patch_profile(stored_profile(), request_body('{"phone": null}'))
        self.assertEqual(updated["phone"], "+91-98000-00000")


class FixMissingVersusNull(unittest.TestCase):
    def test_renaming_keeps_phone_and_bio(self):
        updated = apply_patch(stored_profile(), request_body('{"display_name": "Ann"}'))
        self.assertEqual(updated, {**stored_profile(), "display_name": "Ann"})

    def test_explicit_null_clears_a_nullable_field(self):
        updated = apply_patch(stored_profile(), request_body('{"phone": null}'))
        self.assertIsNone(updated["phone"])
        self.assertEqual(updated["bio"], "Runner, reader.")

    def test_empty_body_changes_nothing(self):
        self.assertEqual(apply_patch(stored_profile(), {}), stored_profile())

    def test_empty_string_is_a_value_not_a_clear(self):
        updated = apply_patch(stored_profile(), {"bio": ""})
        self.assertEqual(updated["bio"], "")

    def test_stored_profile_is_not_mutated(self):
        profile = stored_profile()
        apply_patch(profile, {"bio": None})
        self.assertEqual(profile, stored_profile())

    def test_parse_patch_only_returns_sent_fields(self):
        self.assertEqual(parse_patch({"phone": None}), {"phone": None})

    def test_missing_sentinel_is_not_none(self):
        self.assertIsNot(MISSING, None)
        self.assertEqual(repr(MISSING), "MISSING")


class FixValidation(unittest.TestCase):
    def assert_rejected(self, body) -> None:
        with self.assertRaises(PatchValidationError):
            apply_patch(stored_profile(), body)

    def test_null_for_required_field_is_rejected(self):
        self.assert_rejected({"display_name": None})

    def test_unknown_field_is_rejected(self):
        self.assert_rejected({"phnoe": "123"})

    def test_client_cannot_patch_user_id(self):
        self.assert_rejected({"user_id": 1})

    def test_wrong_type_is_rejected(self):
        self.assert_rejected({"phone": 9800000000})

    def test_boolean_is_not_accepted_as_string(self):
        self.assert_rejected({"bio": True})

    def test_too_long_value_is_rejected(self):
        self.assert_rejected({"display_name": "x" * 51})

    def test_non_object_body_is_rejected(self):
        for body in ([], None, "name", 3):
            self.assert_rejected(body)

    def test_one_bad_field_applies_nothing(self):
        profile = stored_profile()
        with self.assertRaises(PatchValidationError):
            apply_patch(profile, {"bio": "new bio", "display_name": None})
        self.assertEqual(profile["bio"], "Runner, reader.")


if __name__ == "__main__":
    unittest.main()
