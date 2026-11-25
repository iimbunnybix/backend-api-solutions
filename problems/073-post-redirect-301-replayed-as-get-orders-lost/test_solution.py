import unittest

from solution import (STATUS_CREATED, STATUS_MOVED_PERMANENTLY, STATUS_OK, STATUS_PERMANENT_REDIRECT,
                      CANONICAL_ORDERS_PATH, OrderStore, Request, Response, client_send,
                      follow_up_request, make_server, naive_redirect_status, redirect_status)

ORDER_BODY = b'{"sku": "tea-42", "qty": 2}'
NON_CANONICAL_PATH = "/v1/orders"


def naive_setup():
    """A server with the original 301-for-everything redirect."""
    store = OrderStore()
    return store, make_server(store, naive_redirect_status)


def fixed_setup():
    """A server with the method-aware redirect."""
    store = OrderStore()
    return store, make_server(store, redirect_status)


class ReproduceProblem(unittest.TestCase):
    def test_post_to_canonical_path_creates_the_order(self):
        store, serve = naive_setup()
        response = client_send(serve, Request("POST", CANONICAL_ORDERS_PATH, ORDER_BODY))
        self.assertEqual(response.status, STATUS_CREATED)
        self.assertEqual(store.orders, [ORDER_BODY])

    def test_post_without_trailing_slash_creates_no_order(self):
        store, serve = naive_setup()
        client_send(serve, Request("POST", NON_CANONICAL_PATH, ORDER_BODY))
        self.assertEqual(store.orders, [])

    def test_lost_order_still_looks_successful_to_the_client(self):
        _, serve = naive_setup()
        response = client_send(serve, Request("POST", NON_CANONICAL_PATH, ORDER_BODY))
        # The replayed GET hits the listing and returns 200, so the app shows success.
        self.assertEqual(response.status, STATUS_OK)


class FixMethodAwareRedirect(unittest.TestCase):
    def test_post_without_trailing_slash_creates_the_order(self):
        store, serve = fixed_setup()
        response = client_send(serve, Request("POST", NON_CANONICAL_PATH, ORDER_BODY))
        self.assertEqual(response.status, STATUS_CREATED)
        self.assertEqual(store.orders, [ORDER_BODY])

    def test_post_gets_a_308_redirect(self):
        _, serve = fixed_setup()
        response = serve(Request("POST", NON_CANONICAL_PATH, ORDER_BODY))
        self.assertEqual(response.status, STATUS_PERMANENT_REDIRECT)
        self.assertEqual(response.location, CANONICAL_ORDERS_PATH)

    def test_get_still_gets_a_301_redirect(self):
        _, serve = fixed_setup()
        response = serve(Request("GET", NON_CANONICAL_PATH))
        self.assertEqual(response.status, STATUS_MOVED_PERMANENTLY)

    def test_put_and_delete_get_308(self):
        for method in ("PUT", "PATCH", "DELETE"):
            with self.subTest(method=method):
                self.assertEqual(redirect_status(method), STATUS_PERMANENT_REDIRECT)

    def test_lowercase_method_names_are_handled(self):
        self.assertEqual(redirect_status("get"), STATUS_MOVED_PERMANENTLY)
        self.assertEqual(redirect_status("post"), STATUS_PERMANENT_REDIRECT)

    def test_order_is_created_exactly_once(self):
        store, serve = fixed_setup()
        client_send(serve, Request("POST", NON_CANONICAL_PATH, ORDER_BODY))
        self.assertEqual(len(store.orders), 1)


class ClientRedirectRules(unittest.TestCase):
    def test_302_turns_post_into_bodyless_get(self):
        follow = follow_up_request(Request("POST", "/a", ORDER_BODY), Response(302, location="/b"))
        self.assertEqual((follow.method, follow.body), ("GET", b""))

    def test_303_turns_put_into_get(self):
        follow = follow_up_request(Request("PUT", "/a", ORDER_BODY), Response(303, location="/b"))
        self.assertEqual(follow.method, "GET")

    def test_307_keeps_method_and_body(self):
        follow = follow_up_request(Request("POST", "/a", ORDER_BODY), Response(307, location="/b"))
        self.assertEqual((follow.method, follow.body, follow.path), ("POST", ORDER_BODY, "/b"))


if __name__ == "__main__":
    unittest.main()
