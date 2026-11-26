import unittest

from solution import (MAX_ATTEMPTS, NETWORK_ERROR, FakeReceiver, Outcome, classify, deliver,
                      naive_classify)

EVENT_ID = "evt_1001"


def receiver_answering(*statuses: int) -> FakeReceiver:
    """A receiver that replies with ``statuses`` in order, then repeats the last."""
    return FakeReceiver(list(statuses))


class ReproduceProblem(unittest.TestCase):
    def test_204_receiver_processes_the_event_six_times(self):
        receiver = receiver_answering(204)
        deliver(EVENT_ID, receiver, naive_classify)
        self.assertEqual(receiver.processed, [EVENT_ID] * MAX_ATTEMPTS)

    def test_202_delivery_is_reported_as_failed_after_all_attempts(self):
        result = deliver(EVENT_ID, receiver_answering(202), naive_classify)
        self.assertEqual(result.outcome, Outcome.GIVE_UP)
        self.assertEqual(result.attempts, MAX_ATTEMPTS)

    def test_permanent_404_is_retried_every_time(self):
        receiver = receiver_answering(404)
        deliver(EVENT_ID, receiver, naive_classify)
        self.assertEqual(receiver.calls, MAX_ATTEMPTS)


class FixClassifyByStatusClass(unittest.TestCase):
    def test_every_2xx_status_is_delivered(self):
        for status in (200, 201, 202, 204):
            with self.subTest(status=status):
                self.assertEqual(classify(status), Outcome.DELIVERED)

    def test_server_errors_and_throttling_are_retried(self):
        for status in (500, 502, 503, 408, 429, NETWORK_ERROR):
            with self.subTest(status=status):
                self.assertEqual(classify(status), Outcome.RETRY)

    def test_other_client_errors_are_not_retried(self):
        for status in (400, 401, 404, 410, 422):
            with self.subTest(status=status):
                self.assertEqual(classify(status), Outcome.GIVE_UP)

    def test_redirects_are_not_followed_or_retried(self):
        self.assertEqual(classify(301), Outcome.GIVE_UP)


class FixDelivery(unittest.TestCase):
    def test_204_receiver_processes_the_event_once(self):
        receiver = receiver_answering(204)
        result = deliver(EVENT_ID, receiver)
        self.assertEqual(receiver.processed, [EVENT_ID])
        self.assertEqual(result.outcome, Outcome.DELIVERED)

    def test_404_gives_up_after_one_attempt(self):
        result = deliver(EVENT_ID, receiver_answering(404))
        self.assertEqual((result.outcome, result.attempts), (Outcome.GIVE_UP, 1))

    def test_temporary_outage_is_retried_until_it_succeeds(self):
        receiver = receiver_answering(503, NETWORK_ERROR, 202)
        result = deliver(EVENT_ID, receiver)
        self.assertEqual((result.outcome, result.attempts), (Outcome.DELIVERED, 3))
        self.assertEqual(receiver.processed, [EVENT_ID])

    def test_endless_outage_stops_at_the_attempt_limit(self):
        result = deliver(EVENT_ID, receiver_answering(500))
        self.assertEqual((result.outcome, result.attempts), (Outcome.GIVE_UP, MAX_ATTEMPTS))

    def test_every_attempt_carries_the_same_event_id(self):
        receiver = receiver_answering(500, 500, 200)
        deliver(EVENT_ID, receiver)
        self.assertEqual(receiver.processed, [EVENT_ID])


if __name__ == "__main__":
    unittest.main()
