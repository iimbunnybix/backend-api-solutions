import json
import unittest
from datetime import datetime, timedelta, timezone

from solution import (
    NaiveDatetimeError,
    format_api_timestamp,
    naive_serialize_appointment,
    parse_api_timestamp,
    parse_like_client,
    serialize_appointment,
    to_utc,
)

# Fixed offsets keep the tests independent of the machine's tz database.
INDIA = timezone(timedelta(hours=5, minutes=30))
NEW_YORK_WINTER = timezone(timedelta(hours=-5))

# The real slot: 09:00 UTC on 10 March 2026.
SLOT_UTC = datetime(2026, 3, 10, 9, 0, tzinfo=timezone.utc)
# What the original code stored: the same wall-clock digits, offset dropped.
SLOT_NAIVE = datetime(2026, 3, 10, 9, 0)


def starts_at_seen_by(response_json: str, client_timezone: timezone) -> datetime:
    return parse_like_client(json.loads(response_json)["starts_at"], client_timezone)


class ReproduceProblem(unittest.TestCase):
    def test_naive_timestamp_has_no_offset_in_the_response(self):
        body = json.loads(naive_serialize_appointment(1, SLOT_NAIVE))
        self.assertEqual(body["starts_at"], "2026-03-10T09:00:00")

    def test_patient_in_india_sees_the_slot_five_and_a_half_hours_early(self):
        seen = starts_at_seen_by(naive_serialize_appointment(1, SLOT_NAIVE), INDIA)
        self.assertEqual(seen.hour, 9)
        self.assertEqual(SLOT_UTC - seen, timedelta(hours=5, minutes=30))

    def test_two_clients_disagree_about_the_same_appointment(self):
        response = naive_serialize_appointment(1, SLOT_NAIVE)
        in_india = starts_at_seen_by(response, INDIA)
        in_new_york = starts_at_seen_by(response, NEW_YORK_WINTER)
        self.assertEqual(in_new_york - in_india, timedelta(hours=10, minutes=30))


class FixResponsesCarryTheOffset(unittest.TestCase):
    def test_response_timestamp_ends_with_utc_offset(self):
        body = json.loads(serialize_appointment(1, SLOT_UTC))
        self.assertEqual(body["starts_at"], "2026-03-10T09:00:00+00:00")

    def test_every_client_sees_the_same_instant(self):
        response = serialize_appointment(1, SLOT_UTC)
        self.assertEqual(starts_at_seen_by(response, INDIA), SLOT_UTC)
        self.assertEqual(starts_at_seen_by(response, NEW_YORK_WINTER), SLOT_UTC)

    def test_patient_in_india_sees_local_time_half_past_two(self):
        seen = starts_at_seen_by(serialize_appointment(1, SLOT_UTC), INDIA)
        self.assertEqual((seen.hour, seen.minute), (14, 30))

    def test_non_utc_aware_input_is_converted_to_utc_for_output(self):
        slot_in_india = SLOT_UTC.astimezone(INDIA)
        self.assertEqual(format_api_timestamp(slot_in_india), "2026-03-10T09:00:00+00:00")

    def test_serializing_a_naive_datetime_is_refused(self):
        with self.assertRaises(NaiveDatetimeError):
            serialize_appointment(1, SLOT_NAIVE)


class FixRequestsMustCarryTheOffset(unittest.TestCase):
    def test_naive_timestamp_in_a_request_is_rejected(self):
        with self.assertRaises(NaiveDatetimeError):
            parse_api_timestamp("2026-03-10T09:00:00")

    def test_z_suffix_is_accepted_as_utc(self):
        self.assertEqual(parse_api_timestamp("2026-03-10T09:00:00Z"), SLOT_UTC)

    def test_local_offset_is_converted_to_utc_for_storage(self):
        stored = parse_api_timestamp("2026-03-10T14:30:00+05:30")
        self.assertEqual(stored, SLOT_UTC)
        self.assertEqual(stored.utcoffset(), timedelta(0))

    def test_garbage_timestamp_is_rejected(self):
        with self.assertRaises(NaiveDatetimeError):
            parse_api_timestamp("next tuesday")

    def test_request_to_response_round_trip_keeps_the_instant(self):
        stored = parse_api_timestamp("2026-03-09T23:00:00-05:00")
        self.assertEqual(format_api_timestamp(stored), "2026-03-10T04:00:00+00:00")

    def test_to_utc_refuses_naive_values(self):
        with self.assertRaises(NaiveDatetimeError):
            to_utc(SLOT_NAIVE)


if __name__ == "__main__":
    unittest.main()
