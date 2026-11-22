import unittest

from solution import (
    HTTP_BAD_REQUEST,
    HTTP_NOT_FOUND,
    HTTP_OK,
    UserDirectory,
    handle,
    lookup_url,
    naive_handle,
    naive_lookup_url,
)

TAGGED_EMAIL = "maria+shop@example.com"


def directory():
    return UserDirectory(["maria@example.com", TAGGED_EMAIL, "o'neil&co@example.com"])


class ReproduceProblem(unittest.TestCase):
    def test_raw_plus_arrives_as_space_and_user_is_not_found(self):
        response = naive_handle(naive_lookup_url(TAGGED_EMAIL), directory())
        self.assertEqual(response.status, HTTP_NOT_FOUND)

    def test_raw_ampersand_cuts_the_email_short(self):
        response = naive_handle(naive_lookup_url("o'neil&co@example.com"), directory())
        self.assertEqual(response.status, HTTP_NOT_FOUND)


class FixClientEncodesValues(unittest.TestCase):
    def test_plus_is_percent_encoded(self):
        self.assertEqual(lookup_url(TAGGED_EMAIL), "/users?email=maria%2Bshop@example.com")

    def test_tagged_email_is_found(self):
        response = handle(lookup_url(TAGGED_EMAIL), directory())
        self.assertEqual(response.status, HTTP_OK)
        self.assertEqual(response.body, {"id": 2, "email": TAGGED_EMAIL})

    def test_tagged_email_is_not_confused_with_untagged_one(self):
        self.assertEqual(handle(lookup_url("maria@example.com"), directory()).body["id"], 1)

    def test_ampersand_and_apostrophe_survive(self):
        response = handle(lookup_url("o'neil&co@example.com"), directory())
        self.assertEqual(response.status, HTTP_OK)

    def test_encoded_url_works_with_the_old_server_too(self):
        self.assertEqual(naive_handle(lookup_url(TAGGED_EMAIL), directory()).status, HTTP_OK)


class FixServerRejectsMangledInput(unittest.TestCase):
    def test_unencoded_plus_gets_400_with_a_hint(self):
        response = handle(naive_lookup_url(TAGGED_EMAIL), directory())
        self.assertEqual(response.status, HTTP_BAD_REQUEST)
        self.assertIn("%2B", response.body["error"])

    def test_missing_email_gets_400(self):
        self.assertEqual(handle("/users", directory()).status, HTTP_BAD_REQUEST)

    def test_empty_email_gets_400(self):
        self.assertEqual(handle("/users?email=", directory()).status, HTTP_BAD_REQUEST)

    def test_repeated_email_param_gets_400(self):
        url = "/users?email=maria%40example.com&email=x%40example.com"
        self.assertEqual(handle(url, directory()).status, HTTP_BAD_REQUEST)

    def test_value_without_at_sign_gets_400(self):
        self.assertEqual(handle("/users?email=maria", directory()).status, HTTP_BAD_REQUEST)

    def test_well_formed_unknown_email_is_still_404(self):
        self.assertEqual(handle(lookup_url("nobody@example.com"), directory()).status, HTTP_NOT_FOUND)

    def test_lookup_is_case_insensitive(self):
        self.assertEqual(handle(lookup_url("Maria+Shop@Example.com"), directory()).status, HTTP_OK)


if __name__ == "__main__":
    unittest.main()
