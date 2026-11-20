import json
import unittest

from solution import (
    REPLAY_BUFFER_SIZE,
    RESYNC_EVENT,
    NotificationHub,
    format_frame,
    parse_last_event_id,
)

USER = "user-1"
OTHER_USER = "user-2"


def parse_frames(connection):
    """Turn a connection's SSE text back into (event name, id, payload) tuples."""
    messages = []
    for frame in connection.frames:
        fields = {}
        for line in frame.strip().split("\n"):
            name, _, value = line.partition(": ")
            fields[name] = value
        messages.append((fields["event"], int(fields["id"]), json.loads(fields["data"])))
    return messages


def received_texts(connection):
    """Only the notification texts a connection received, in order."""
    return [payload["text"] for name, _, payload in parse_frames(connection) if name != RESYNC_EVENT]


def last_seen_id(connection):
    """What a browser's EventSource would send as Last-Event-ID."""
    return str(parse_frames(connection)[-1][1])


def notify(hub, user, text):
    """Publish one notification with a text payload."""
    return hub.publish(user, {"text": text})


class ReproduceProblem(unittest.TestCase):
    def test_events_published_while_disconnected_are_never_delivered(self):
        hub = NotificationHub()
        first = hub.naive_connect(USER)
        notify(hub, USER, "order confirmed")
        hub.disconnect(first)
        notify(hub, USER, "order shipped")
        notify(hub, USER, "driver nearby")
        second = hub.naive_connect(USER)
        notify(hub, USER, "delivered")
        delivered = received_texts(first) + received_texts(second)
        self.assertEqual(delivered, ["order confirmed", "delivered"])


class FixResumesFromLastEventId(unittest.TestCase):
    def setUp(self):
        self.hub = NotificationHub()
        self.first = self.hub.connect(USER)
        notify(self.hub, USER, "order confirmed")
        self.hub.disconnect(self.first)

    def test_missed_events_are_replayed_in_order_on_reconnect(self):
        notify(self.hub, USER, "order shipped")
        notify(self.hub, USER, "driver nearby")
        second = self.hub.connect(USER, last_seen_id(self.first))
        self.assertEqual(received_texts(second), ["order shipped", "driver nearby"])

    def test_events_the_client_already_saw_are_not_sent_again(self):
        second = self.hub.connect(USER, last_seen_id(self.first))
        self.assertEqual(second.frames, [])

    def test_live_events_follow_the_replay(self):
        notify(self.hub, USER, "order shipped")
        second = self.hub.connect(USER, last_seen_id(self.first))
        notify(self.hub, USER, "delivered")
        self.assertEqual(received_texts(second), ["order shipped", "delivered"])

    def test_other_users_events_are_not_replayed(self):
        notify(self.hub, OTHER_USER, "someone else's order")
        second = self.hub.connect(USER, last_seen_id(self.first))
        self.assertEqual(second.frames, [])

    def test_closed_connection_receives_nothing_more(self):
        notify(self.hub, USER, "order shipped")
        self.assertEqual(received_texts(self.first), ["order confirmed"])

    def test_first_connection_without_header_gets_no_replay(self):
        fresh = self.hub.connect(USER)
        self.assertEqual(fresh.frames, [])


class FixEventIds(unittest.TestCase):
    def test_ids_increase_across_users(self):
        hub = NotificationHub()
        ids = [notify(hub, user, "x").event_id for user in (USER, OTHER_USER, USER)]
        self.assertEqual(ids, [1, 2, 3])

    def test_frame_uses_sse_wire_format(self):
        self.assertEqual(format_frame("notification", 7, {"text": "hi"}),
                         'id: 7\nevent: notification\ndata: {"text":"hi"}\n\n')

    def test_header_with_spaces_is_accepted(self):
        self.assertEqual(parse_last_event_id(" 42 "), 42)

    def test_non_numeric_header_is_rejected(self):
        self.assertIsNone(parse_last_event_id("abc"))


class FixAsksForResyncWhenGapIsLost(unittest.TestCase):
    def setUp(self):
        self.hub = NotificationHub()
        self.first = self.hub.connect(USER)
        notify(self.hub, USER, "seen")
        self.hub.disconnect(self.first)

    def test_gap_larger_than_the_buffer_sends_resync(self):
        for number in range(REPLAY_BUFFER_SIZE + 1):
            notify(self.hub, USER, f"missed {number}")
        second = self.hub.connect(USER, last_seen_id(self.first))
        self.assertEqual([name for name, _, _ in parse_frames(second)], [RESYNC_EVENT])

    def test_gap_exactly_the_buffer_size_is_still_replayed(self):
        for number in range(REPLAY_BUFFER_SIZE):
            notify(self.hub, USER, f"missed {number}")
        second = self.hub.connect(USER, last_seen_id(self.first))
        self.assertEqual(len(received_texts(second)), REPLAY_BUFFER_SIZE)

    def test_resync_id_lets_the_next_reconnect_resume(self):
        for number in range(REPLAY_BUFFER_SIZE + 1):
            notify(self.hub, USER, f"missed {number}")
        second = self.hub.connect(USER, last_seen_id(self.first))
        self.hub.disconnect(second)
        notify(self.hub, USER, "after resync")
        third = self.hub.connect(USER, last_seen_id(second))
        self.assertEqual(received_texts(third), ["after resync"])

    def test_id_the_server_never_issued_sends_resync(self):
        second = self.hub.connect(USER, "999")
        self.assertEqual(parse_frames(second)[0][0], RESYNC_EVENT)

    def test_garbage_header_sends_resync(self):
        second = self.hub.connect(USER, "not-a-number")
        self.assertEqual(parse_frames(second)[0][0], RESYNC_EVENT)

    def test_other_users_traffic_does_not_evict_this_users_events(self):
        for number in range(REPLAY_BUFFER_SIZE * 2):
            notify(self.hub, OTHER_USER, f"other {number}")
        notify(self.hub, USER, "mine")
        second = self.hub.connect(USER, last_seen_id(self.first))
        self.assertEqual(received_texts(second), ["mine"])


if __name__ == "__main__":
    unittest.main()
