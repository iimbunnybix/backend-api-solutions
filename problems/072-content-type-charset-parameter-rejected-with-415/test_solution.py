import json
import unittest

from solution import (STATUS_BAD_REQUEST, STATUS_OK, STATUS_UNSUPPORTED_MEDIA_TYPE,
                      check_json_content_type, handle_create_order, naive_handle_create_order,
                      parse_media_type)

ORDER_BODY = json.dumps({"order_id": "ord-72", "items": ["café au lait"]}).encode("utf-8")
OLD_APP_HEADER = "application/json"
NEW_APP_HEADER = "application/json; charset=utf-8"


def accepted(content_type):
    """True if the fixed check lets the body through."""
    return check_json_content_type(content_type) is None


class ReproduceProblem(unittest.TestCase):
    def test_old_app_header_is_accepted(self):
        status, _ = naive_handle_create_order(OLD_APP_HEADER, ORDER_BODY)
        self.assertEqual(status, STATUS_OK)

    def test_new_app_header_with_charset_gets_415(self):
        status, _ = naive_handle_create_order(NEW_APP_HEADER, ORDER_BODY)
        self.assertEqual(status, STATUS_UNSUPPORTED_MEDIA_TYPE)

    def test_upper_case_media_type_gets_415(self):
        status, _ = naive_handle_create_order("Application/JSON", ORDER_BODY)
        self.assertEqual(status, STATUS_UNSUPPORTED_MEDIA_TYPE)


class FixMediaTypeParsing(unittest.TestCase):
    def test_new_app_header_with_charset_is_accepted(self):
        status, payload = handle_create_order(NEW_APP_HEADER, ORDER_BODY)
        self.assertEqual((status, payload), (STATUS_OK, {"accepted": "ord-72"}))

    def test_media_type_and_charset_are_case_insensitive(self):
        self.assertTrue(accepted("Application/JSON; Charset=UTF-8"))

    def test_quoted_charset_is_accepted(self):
        self.assertTrue(accepted('application/json; charset="utf-8"'))

    def test_extra_whitespace_and_trailing_semicolon_are_accepted(self):
        self.assertTrue(accepted("  application/json ;  charset=utf-8 ; "))

    def test_json_suffix_types_are_accepted(self):
        self.assertTrue(accepted("application/merge-patch+json"))
        self.assertTrue(accepted("application/vnd.api+json; charset=utf-8"))

    def test_parameters_are_parsed_into_a_dict(self):
        media_type = parse_media_type('application/json; Charset="UTF-8"; version=2')
        self.assertEqual(media_type.essence, "application/json")
        self.assertEqual(media_type.parameters, {"charset": "UTF-8", "version": "2"})


class FixStillRejectsWhatItShould(unittest.TestCase):
    def test_missing_content_type_gets_415(self):
        status, _ = handle_create_order(None, ORDER_BODY)
        self.assertEqual(status, STATUS_UNSUPPORTED_MEDIA_TYPE)

    def test_non_json_media_type_gets_415(self):
        self.assertFalse(accepted("text/plain; charset=utf-8"))
        self.assertFalse(accepted("application/x-www-form-urlencoded"))

    def test_lookalike_media_type_is_not_json(self):
        self.assertFalse(accepted("application/jsonp"))
        self.assertFalse(accepted("text/x+json"))

    def test_non_utf8_charset_gets_415_with_a_reason(self):
        status, payload = handle_create_order("application/json; charset=iso-8859-1", ORDER_BODY)
        self.assertEqual(status, STATUS_UNSUPPORTED_MEDIA_TYPE)
        self.assertIn("UTF-8", payload["error"])

    def test_malformed_header_is_rejected(self):
        for header in ("", "json", "application/", "/json", "a/b/c"):
            self.assertFalse(accepted(header), header)

    def test_invalid_body_gets_400_not_500(self):
        status, _ = handle_create_order(NEW_APP_HEADER, b"\xff{not json")
        self.assertEqual(status, STATUS_BAD_REQUEST)


if __name__ == "__main__":
    unittest.main()
