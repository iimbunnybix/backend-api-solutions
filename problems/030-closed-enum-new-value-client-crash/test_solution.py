import unittest

from solution import (STATUS_FIELD, UNKNOWN_STATUS_LABEL, OrderStatus, UnknownValueCounter,
                      can_cancel, naive_parse_order, naive_parse_order_list, parse_order,
                      parse_order_list, parse_status, status_label)

NEW_SERVER_STATUS = "READY_FOR_PICKUP"
HISTORY_SIZE = 50


def order_payload(order_id, status):
    return {"id": order_id, STATUS_FIELD: status, "total_cents": 2599}


def order_history_with_one_new_status():
    """A customer's 50 orders, the latest one using a status added after the app shipped."""
    payloads = [order_payload(f"ord-{n}", "DELIVERED") for n in range(HISTORY_SIZE - 1)]
    payloads.append(order_payload("ord-new", NEW_SERVER_STATUS))
    return payloads


class ReproduceProblem(unittest.TestCase):
    def test_new_status_value_raises(self):
        with self.assertRaises(ValueError):
            naive_parse_order(order_payload("ord-1", NEW_SERVER_STATUS))

    def test_one_new_status_fails_the_whole_order_history(self):
        with self.assertRaises(ValueError):
            naive_parse_order_list(order_history_with_one_new_status())

    def test_known_statuses_still_parse(self):
        order = naive_parse_order(order_payload("ord-1", "SHIPPED"))
        self.assertIs(order.status, OrderStatus.SHIPPED)


class FixTolerantReader(unittest.TestCase):
    def setUp(self):
        self.counter = UnknownValueCounter()

    def test_new_status_value_becomes_unknown(self):
        order = parse_order(order_payload("ord-1", NEW_SERVER_STATUS), self.counter)
        self.assertIs(order.status, OrderStatus.UNKNOWN)

    def test_raw_value_is_kept_for_support_and_logs(self):
        order = parse_order(order_payload("ord-1", NEW_SERVER_STATUS), self.counter)
        self.assertEqual(order.raw_status, NEW_SERVER_STATUS)

    def test_whole_history_loads_with_one_new_status(self):
        orders = parse_order_list(order_history_with_one_new_status(), self.counter)
        self.assertEqual(len(orders), HISTORY_SIZE)
        self.assertIs(orders[0].status, OrderStatus.DELIVERED)

    def test_unknown_values_are_counted_for_alerting(self):
        parse_order_list(order_history_with_one_new_status() * 3, self.counter)
        self.assertEqual(self.counter.counts, {(STATUS_FIELD, NEW_SERVER_STATUS): 3})

    def test_unknown_status_gets_a_neutral_label(self):
        order = parse_order(order_payload("ord-1", NEW_SERVER_STATUS))
        self.assertEqual(status_label(order), UNKNOWN_STATUS_LABEL)

    def test_unknown_status_is_never_cancellable(self):
        order = parse_order(order_payload("ord-1", NEW_SERVER_STATUS))
        self.assertFalse(can_cancel(order))

    def test_known_cancellable_status_still_offers_cancel(self):
        self.assertTrue(can_cancel(parse_order(order_payload("ord-1", "PAID"))))

    def test_server_cannot_claim_the_client_placeholder(self):
        parse_status("UNKNOWN", self.counter)
        self.assertEqual(self.counter.counts, {(STATUS_FIELD, "UNKNOWN"): 1})

    def test_values_are_case_sensitive(self):
        self.assertIs(parse_status("shipped"), OrderStatus.UNKNOWN)

    def test_non_string_status_is_still_an_error(self):
        for broken in (None, 3, ["PAID"]):
            with self.assertRaises(ValueError, msg=repr(broken)):
                parse_status(broken)

    def test_every_known_status_has_a_label(self):
        for member in OrderStatus:
            order = parse_order(order_payload("ord-1", member.value))
            self.assertIsInstance(status_label(order), str)


if __name__ == "__main__":
    unittest.main()
