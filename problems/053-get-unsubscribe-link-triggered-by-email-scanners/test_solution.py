import unittest

from solution import (HTTP_BAD_REQUEST, HTTP_METHOD_NOT_ALLOWED, HTTP_NOT_FOUND, HTTP_OK,
                      MailingList, Request, naive_unsubscribe_handler,
                      unsubscribe_email_headers, unsubscribe_handler)

ALICE = "alice@example.com"
ALICE_TOKEN = "unsub-alice-0001"


class LinkScanner:
    """Fake mail-gateway scanner: fetches every link in an email with HEAD and GET."""

    def scan(self, handler, mailing_list: MailingList, token: str) -> None:
        for method in ("HEAD", "GET"):
            handler(mailing_list, Request(method, query={"token": token}))


def list_with_alice() -> MailingList:
    mailing_list = MailingList()
    mailing_list.add(ALICE, ALICE_TOKEN)
    return mailing_list


def post(form: dict[str, str], token: str = ALICE_TOKEN) -> Request:
    return Request("POST", query={"token": token}, form=form)


class ReproduceProblem(unittest.TestCase):
    def test_scanner_fetching_the_link_unsubscribes_the_user(self):
        mailing_list = list_with_alice()
        LinkScanner().scan(naive_unsubscribe_handler, mailing_list, ALICE_TOKEN)
        self.assertFalse(mailing_list.is_subscribed(ALICE))

    def test_even_a_head_request_unsubscribes(self):
        mailing_list = list_with_alice()
        naive_unsubscribe_handler(mailing_list, Request("HEAD", query={"token": ALICE_TOKEN}))
        self.assertFalse(mailing_list.is_subscribed(ALICE))


class FixSafeGet(unittest.TestCase):
    def test_scanner_fetching_the_link_keeps_the_subscription(self):
        mailing_list = list_with_alice()
        LinkScanner().scan(unsubscribe_handler, mailing_list, ALICE_TOKEN)
        self.assertTrue(mailing_list.is_subscribed(ALICE))

    def test_get_shows_a_confirmation_form_that_posts(self):
        response = unsubscribe_handler(list_with_alice(), Request("GET", query={"token": ALICE_TOKEN}))
        self.assertEqual(response.status, HTTP_OK)
        self.assertIn('method="post"', response.body)

    def test_head_returns_no_body(self):
        response = unsubscribe_handler(list_with_alice(), Request("HEAD", query={"token": ALICE_TOKEN}))
        self.assertEqual(response.body, "")

    def test_confirm_button_post_unsubscribes(self):
        mailing_list = list_with_alice()
        response = unsubscribe_handler(mailing_list, post({"confirm": "yes"}))
        self.assertEqual(response.status, HTTP_OK)
        self.assertFalse(mailing_list.is_subscribed(ALICE))

    def test_rfc8058_one_click_post_unsubscribes(self):
        mailing_list = list_with_alice()
        unsubscribe_handler(mailing_list, post({"List-Unsubscribe": "One-Click"}))
        self.assertFalse(mailing_list.is_subscribed(ALICE))

    def test_post_without_confirmation_is_rejected(self):
        mailing_list = list_with_alice()
        response = unsubscribe_handler(mailing_list, post({}))
        self.assertEqual(response.status, HTTP_BAD_REQUEST)
        self.assertTrue(mailing_list.is_subscribed(ALICE))

    def test_repeated_post_is_harmless(self):
        mailing_list = list_with_alice()
        unsubscribe_handler(mailing_list, post({"confirm": "yes"}))
        response = unsubscribe_handler(mailing_list, post({"confirm": "yes"}))
        self.assertEqual(response.status, HTTP_OK)

    def test_unknown_token_is_not_found(self):
        response = unsubscribe_handler(list_with_alice(), post({"confirm": "yes"}, token="nope"))
        self.assertEqual(response.status, HTTP_NOT_FOUND)

    def test_other_methods_get_405_with_allow_header(self):
        response = unsubscribe_handler(list_with_alice(), Request("DELETE", query={"token": ALICE_TOKEN}))
        self.assertEqual(response.status, HTTP_METHOD_NOT_ALLOWED)
        self.assertEqual(response.headers["Allow"], "GET, HEAD, POST")

    def test_email_headers_advertise_one_click_post(self):
        headers = unsubscribe_email_headers("https://news.example.com/unsubscribe?token=t")
        self.assertEqual(headers["List-Unsubscribe-Post"], "List-Unsubscribe=One-Click")


if __name__ == "__main__":
    unittest.main()
