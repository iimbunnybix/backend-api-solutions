import unittest

from solution import (OLD_STATUSES, ORDER_API, UnsupportedVersion, VersionChange,
                      VersionedAPI)

LATEST_ORDER = {
    "id": "ord_1",
    "amount": {"value": 1250, "currency": "USD"},
    "customer": {"id": "cus_9", "name": "Asha"},
    "status": "partially_refunded",
}


def old_android_client_parse(body):
    """What the 2025 mobile app does with an order response."""
    price = float(body["price"])                 # KeyError if renamed
    if body["status"] not in OLD_STATUSES:       # strict enum decoding
        raise ValueError(f"unknown status {body['status']}")
    return price, body["customer_name"], body["status"]


def get_order(_req):
    return LATEST_ORDER


class ReproduceBreakingChange(unittest.TestCase):
    def test_unversioned_latest_response_crashes_old_client(self):
        with self.assertRaises(KeyError):
            old_android_client_parse(LATEST_ORDER)

    def test_new_enum_value_alone_breaks_old_client(self):
        # Even with the old field names, an additive enum value is breaking
        # for clients that decode enums strictly.
        body = {"id": "ord_1", "price": "12.50", "customer_name": "Asha",
                "status": "partially_refunded"}
        with self.assertRaises(ValueError):
            old_android_client_parse(body)


class Versioning(unittest.TestCase):
    def test_old_client_gets_old_shape(self):
        status, headers, body = ORDER_API.handle(get_order, None, None, pinned="2025-11-01")
        self.assertEqual(status, 200)
        self.assertEqual(body, {"id": "ord_1", "price": "12.50",
                                "customer_name": "Asha", "status": "refunded"})
        self.assertEqual(old_android_client_parse(body), (12.5, "Asha", "refunded"))
        self.assertEqual(headers["Api-Version"], "2025-11-01")
        self.assertEqual(headers["Deprecation"], "true")

    def test_latest_client_gets_latest_shape(self):
        _, headers, body = ORDER_API.handle(get_order, None, "2026-09-01", pinned="2025-11-01")
        self.assertEqual(body, LATEST_ORDER)
        self.assertNotIn("Deprecation", headers)

    def test_each_intermediate_version(self):
        mid = ORDER_API.render(LATEST_ORDER, "2026-03-01")
        self.assertEqual(mid["amount"], {"value": 1250, "currency": "USD"})
        self.assertEqual(mid["customer_name"], "Asha")
        self.assertEqual(mid["status"], "refunded")
        self.assertEqual(ORDER_API.render(LATEST_ORDER, "2026-06-15")["customer"]["id"], "cus_9")

    def test_header_overrides_pin(self):
        self.assertEqual(ORDER_API.resolve("2026-06-15", "2025-11-01"), "2026-06-15")

    def test_missing_header_uses_pin_not_latest(self):
        self.assertEqual(ORDER_API.resolve(None, "2026-03-01"), "2026-03-01")
        self.assertEqual(ORDER_API.resolve("", "2026-03-01"), "2026-03-01")

    def test_unknown_or_malformed_version_is_400(self):
        for bad in ["2026-04-01", "v2", "2026-13-01"]:
            status, _, body = ORDER_API.handle(get_order, None, bad, pinned="2025-11-01")
            self.assertEqual(status, 400, bad)
            self.assertIn("Api-Version", body["error"])
        with self.assertRaises(UnsupportedVersion):
            ORDER_API.resolve("nope", "2025-11-01")

    def test_render_does_not_mutate_source(self):
        ORDER_API.render(LATEST_ORDER, "2025-11-01")
        self.assertIn("amount", LATEST_ORDER)
        self.assertEqual(LATEST_ORDER["status"], "partially_refunded")

    def test_old_request_body_is_upgraded(self):
        seen = {}

        def create(req):
            seen.update(req)
            return dict(LATEST_ORDER, amount=req["amount"], status="pending")

        _, _, body = ORDER_API.handle(create, {"price": "7.5"}, None, pinned="2025-11-01")
        self.assertEqual(seen["amount"], {"value": 750, "currency": "USD"})
        self.assertEqual(body["price"], "7.50")

    def test_cents_formatting(self):
        order = dict(LATEST_ORDER, amount={"value": 5, "currency": "USD"})
        self.assertEqual(ORDER_API.render(order, "2025-11-01")["price"], "0.05")

    def test_negative_cents_are_formatted_with_the_sign_first(self):
        for cents, price in [(-150, "-1.50"), (-5, "-0.05"), (-100, "-1.00")]:
            order = dict(LATEST_ORDER, amount={"value": cents, "currency": "USD"})
            self.assertEqual(ORDER_API.render(order, "2025-11-01")["price"], price)

    def test_negative_price_is_upgraded_to_negative_cents(self):
        upgraded = ORDER_API.parse_request({"price": "-1.50"}, "2025-11-01")
        self.assertEqual(upgraded["amount"]["value"], -150)

    def test_sub_cent_price_is_rejected_not_truncated(self):
        with self.assertRaises(ValueError):
            ORDER_API.parse_request({"price": "12.999"}, "2025-11-01")

    def test_price_round_trips_through_cents(self):
        for price in ["0.05", "12.50", "-1.50", "-0.05", "100.00"]:
            cents = ORDER_API.parse_request({"price": price}, "2025-11-01")["amount"]
            order = dict(LATEST_ORDER, amount=cents)
            self.assertEqual(ORDER_API.render(order, "2025-11-01")["price"], price)

    def test_non_usd_cannot_be_downgraded(self):
        order = dict(LATEST_ORDER, amount={"value": 100, "currency": "EUR"})
        with self.assertRaises(ValueError):
            ORDER_API.render(order, "2025-11-01")

    def test_changes_sorted_and_validated(self):
        noop = lambda b: b
        api = VersionedAPI("2020-01-01", [VersionChange("2021-01-01", "b", noop),
                                          VersionChange("2020-06-01", "a", noop)])
        self.assertEqual(api.versions, ["2020-01-01", "2020-06-01", "2021-01-01"])
        with self.assertRaises(ValueError):
            VersionedAPI("2022-01-01", [VersionChange("2021-01-01", "x", noop)])


if __name__ == "__main__":
    unittest.main()
