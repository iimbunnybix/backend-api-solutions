import itertools
import unittest

from solution import (STATUS_BAD_REQUEST, STATUS_OK, STATUS_UNSUPPORTED_MEDIA_TYPE,
                      DuplicateHeader, Headers, PaymentService, RawHeaders,
                      handle_payment, media_type, naive_handle_payment)

KEY_COUNTER = itertools.count(1)
BODY = '{"amount_cents": 4999, "card_ref": "test-card-1"}'


def new_idempotency_key() -> str:
    """A fresh key per payment, like the app's per-checkout UUID (deterministic here)."""
    return f"pay-{next(KEY_COUNTER):06d}"


def http1_headers(key: str) -> RawHeaders:
    """Headers as the app sends them over HTTP/1.1 (canonical case)."""
    return [("Content-Type", "application/json"), ("Idempotency-Key", key)]


def through_http2_proxy(raw: RawHeaders) -> RawHeaders:
    """HTTP/2 requires lowercase field names, so the proxy lowercases them."""
    lowered = []
    for name, value in raw:
        lowered.append((name.lower(), value))
    return lowered


class ReproduceProblem(unittest.TestCase):
    def test_direct_http1_request_works(self):
        service = PaymentService()
        status, _ = naive_handle_payment(service, http1_headers("k1"), BODY)
        self.assertEqual(status, STATUS_OK)

    def test_lowercased_content_type_is_rejected_as_unsupported(self):
        raw = through_http2_proxy(http1_headers("k1"))
        status, _ = naive_handle_payment(PaymentService(), raw, BODY)
        self.assertEqual(status, STATUS_UNSUPPORTED_MEDIA_TYPE)

    def test_retry_with_lowercased_key_charges_twice(self):
        service = PaymentService()
        raw = [("Content-Type", "application/json"), ("idempotency-key", "k1")]
        naive_handle_payment(service, raw, BODY)
        naive_handle_payment(service, raw, BODY)
        self.assertEqual(len(service.charges), 2)

    def test_dict_keeps_only_the_last_repeated_header(self):
        raw = [("Accept", "application/json"), ("Accept", "text/html")]
        self.assertEqual(dict(raw), {"Accept": "text/html"})


class FixCaseInsensitiveLookup(unittest.TestCase):
    def test_request_through_http2_proxy_is_accepted(self):
        raw = through_http2_proxy(http1_headers(new_idempotency_key()))
        status, _ = handle_payment(PaymentService(), raw, BODY)
        self.assertEqual(status, STATUS_OK)

    def test_retry_through_proxy_charges_once(self):
        service = PaymentService()
        raw = through_http2_proxy(http1_headers(new_idempotency_key()))
        first = handle_payment(service, raw, BODY)
        second = handle_payment(service, raw, BODY)
        self.assertEqual(first, second)
        self.assertEqual(len(service.charges), 1)

    def test_retry_with_different_header_case_is_still_the_same_payment(self):
        service = PaymentService()
        key = new_idempotency_key()
        handle_payment(service, http1_headers(key), BODY)
        handle_payment(service, through_http2_proxy(http1_headers(key)), BODY)
        self.assertEqual(len(service.charges), 1)

    def test_lookup_ignores_case_in_every_spelling(self):
        headers = Headers([("iDeMpOtEnCy-KeY", "k1")])
        for name in ["Idempotency-Key", "idempotency-key", "IDEMPOTENCY-KEY"]:
            self.assertEqual(headers.get_one(name), "k1")
            self.assertIn(name, headers)

    def test_media_type_ignores_case_and_parameters(self):
        self.assertEqual(media_type("Application/JSON; charset=utf-8"), "application/json")
        self.assertIsNone(media_type(None))

    def test_missing_key_is_rejected_instead_of_charged(self):
        service = PaymentService()
        status, _ = handle_payment(service, [("content-type", "application/json")], BODY)
        self.assertEqual(status, STATUS_BAD_REQUEST)
        self.assertEqual(service.charges, [])

    def test_wrong_media_type_is_still_rejected(self):
        raw = [("content-type", "text/plain"), ("idempotency-key", "k1")]
        status, _ = handle_payment(PaymentService(), raw, BODY)
        self.assertEqual(status, STATUS_UNSUPPORTED_MEDIA_TYPE)


class FixRepeatedHeaders(unittest.TestCase):
    def test_all_repeated_values_are_kept_in_order(self):
        headers = Headers([("Set-Cookie", "a=1; Path=/"), ("set-cookie", "b=2, c")])
        # Set-Cookie must never be comma-joined: its values contain commas.
        self.assertEqual(headers.get_all("SET-COOKIE"), ["a=1; Path=/", "b=2, c"])

    def test_list_header_combines_lines_and_commas(self):
        headers = Headers([("Accept", "application/json, text/html"),
                           ("accept", "text/plain")])
        self.assertEqual(headers.get_list("Accept"),
                         ["application/json", "text/html", "text/plain"])

    def test_conflicting_idempotency_keys_are_rejected(self):
        service = PaymentService()
        raw = [("content-type", "application/json"),
               ("idempotency-key", "k1"), ("Idempotency-Key", "k2")]
        status, body = handle_payment(service, raw, BODY)
        self.assertEqual(status, STATUS_BAD_REQUEST)
        self.assertIn("conflicting", body)
        self.assertEqual(service.charges, [])

    def test_identical_duplicate_single_value_is_accepted(self):
        headers = Headers([("Content-Type", "application/json"),
                           ("content-type", "application/json")])
        self.assertEqual(headers.get_one("Content-Type"), "application/json")

    def test_get_one_raises_on_conflict(self):
        headers = Headers([("Content-Type", "application/json"), ("content-type", "text/xml")])
        with self.assertRaises(DuplicateHeader):
            headers.get_one("content-type")

    def test_original_spelling_is_kept_for_forwarding(self):
        raw = [("X-Request-Id", "r1"), ("x-request-id", "r2")]
        self.assertEqual(Headers(raw).lines(), raw)

    def test_absent_header_gives_none_and_empty_lists(self):
        headers = Headers()
        self.assertIsNone(headers.get_one("Idempotency-Key"))
        self.assertEqual(headers.get_all("Accept"), [])
        self.assertEqual(headers.get_list("Accept"), [])
        self.assertNotIn("Accept", headers)


if __name__ == "__main__":
    unittest.main()
