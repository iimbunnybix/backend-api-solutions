import json
import unittest

from solution import (
    JAVASCRIPT_MAX_SAFE_INTEGER,
    MAX_ID,
    InvalidId,
    is_javascript_safe,
    naive_serialize_order,
    parse_id,
    parse_like_javascript,
    serialize_order,
)

# Two neighbouring Snowflake-style IDs, both far above 2**53.
ORDER_ID = 1891234567890123457
NEIGHBOUR_ORDER_ID = 1891234567890123458


def make_order(order_id: int = ORDER_ID) -> dict:
    return {"id": order_id, "customer_id": 42, "total_cents": 1999, "status": "paid"}


class ReproduceProblem(unittest.TestCase):
    def test_large_id_is_rounded_by_a_javascript_client(self):
        seen_by_browser = parse_like_javascript(naive_serialize_order(make_order()))
        self.assertNotEqual(seen_by_browser["id"], ORDER_ID)
        self.assertEqual(seen_by_browser["id"], 1891234567890123520)

    def test_two_different_orders_look_identical_to_the_browser(self):
        first = parse_like_javascript(naive_serialize_order(make_order(ORDER_ID)))
        second = parse_like_javascript(naive_serialize_order(make_order(NEIGHBOUR_ORDER_ID)))
        self.assertEqual(first["id"], second["id"])

    def test_ids_up_to_max_safe_integer_were_never_affected(self):
        order = make_order(JAVASCRIPT_MAX_SAFE_INTEGER)
        seen_by_browser = parse_like_javascript(naive_serialize_order(order))
        self.assertEqual(seen_by_browser["id"], JAVASCRIPT_MAX_SAFE_INTEGER)


class FixIdsAreStrings(unittest.TestCase):
    def test_large_id_reaches_the_browser_unchanged(self):
        seen_by_browser = parse_like_javascript(serialize_order(make_order()))
        self.assertEqual(seen_by_browser["id"], str(ORDER_ID))

    def test_every_id_field_is_a_string(self):
        body = json.loads(serialize_order(make_order()))
        self.assertEqual(body["customer_id"], "42")

    def test_non_id_numbers_stay_numbers(self):
        body = json.loads(serialize_order(make_order()))
        self.assertEqual(body["total_cents"], 1999)

    def test_serializing_does_not_mutate_the_input(self):
        order = make_order()
        serialize_order(order)
        self.assertEqual(order["id"], ORDER_ID)

    def test_id_round_trips_from_response_back_into_a_request(self):
        id_from_response = json.loads(serialize_order(make_order()))["id"]
        self.assertEqual(parse_id(id_from_response), ORDER_ID)


class FixStrictIdParsing(unittest.TestCase):
    def test_string_id_is_accepted(self):
        self.assertEqual(parse_id(str(ORDER_ID)), ORDER_ID)

    def test_exact_integer_from_an_old_client_is_accepted(self):
        self.assertEqual(parse_id(ORDER_ID), ORDER_ID)

    def test_float_id_is_rejected_because_it_may_already_be_rounded(self):
        with self.assertRaises(InvalidId):
            parse_id(float(ORDER_ID))

    def test_boolean_is_rejected(self):
        with self.assertRaises(InvalidId):
            parse_id(True)

    def test_strings_that_int_would_accept_are_rejected(self):
        for raw in ("", " 12", "+12", "-12", "1_2", "12.0", "1e18", "١٢"):
            with self.subTest(raw=raw), self.assertRaises(InvalidId):
                parse_id(raw)

    def test_zero_and_negative_ids_are_rejected(self):
        for raw in (0, -1, "0"):
            with self.subTest(raw=raw), self.assertRaises(InvalidId):
                parse_id(raw)

    def test_id_above_bigint_range_is_rejected(self):
        self.assertEqual(parse_id(MAX_ID), MAX_ID)
        with self.assertRaises(InvalidId):
            parse_id(str(MAX_ID + 1))

    def test_none_is_rejected(self):
        with self.assertRaises(InvalidId):
            parse_id(None)


class SafeIntegerBoundary(unittest.TestCase):
    def test_boundary_is_two_to_the_53_minus_one(self):
        self.assertTrue(is_javascript_safe(2**53 - 1))
        self.assertFalse(is_javascript_safe(2**53))
        self.assertFalse(is_javascript_safe(ORDER_ID))

    def test_first_unsafe_odd_integer_rounds_in_a_double(self):
        self.assertNotEqual(int(float(2**53 + 1)), 2**53 + 1)


if __name__ == "__main__":
    unittest.main()
