"""Resume a Server-Sent Events stream from Last-Event-ID instead of from "now".

The notification service pushed events to apps over SSE. Mobile connections
drop all the time, and the browser or app reconnects automatically, but the
server started every new connection from "now", so anything published while
the client was disconnected was never delivered.

The fix gives every event a monotonically increasing id, keeps a bounded
replay buffer per user, and replays the events after the Last-Event-ID the
client sends on reconnect. When that id is older than the buffer (or makes no
sense), the server sends a "resync" event telling the client to reload its
data, instead of silently skipping the gap.
"""
from __future__ import annotations

import json
from collections import deque
from dataclasses import dataclass, field

REPLAY_BUFFER_SIZE = 100
NOTIFICATION_EVENT = "notification"
RESYNC_EVENT = "resync"


@dataclass(frozen=True)
class Event:
    event_id: int
    user_id: str
    payload: dict[str, object]


@dataclass
class Connection:
    """One open SSE response; frames are the text written to the client."""

    user_id: str
    frames: list[str] = field(default_factory=list)
    is_open: bool = True


def format_frame(event_name: str, event_id: int, payload: dict[str, object]) -> str:
    """Encode one SSE message; the id line is what the client echoes back."""
    data = json.dumps(payload, separators=(",", ":"))
    return f"id: {event_id}\nevent: {event_name}\ndata: {data}\n\n"


@dataclass
class UserReplayBuffer:
    """The newest events of one user plus the id of the newest one evicted."""

    events: deque[Event] = field(default_factory=lambda: deque(maxlen=REPLAY_BUFFER_SIZE))
    newest_evicted_id: int = 0

    def append(self, event: Event) -> None:
        """Store an event, remembering which id falls out of the buffer."""
        if len(self.events) == self.events.maxlen:
            self.newest_evicted_id = self.events[0].event_id
        self.events.append(event)

    def events_after(self, last_event_id: int) -> list[Event]:
        """Buffered events newer than the client's last one, oldest first."""
        return [event for event in self.events if event.event_id > last_event_id]


def parse_last_event_id(header_value: str | None) -> int | None:
    """The client's Last-Event-ID as an int, or None if absent or not a number."""
    if header_value is None:
        return None
    text = header_value.strip()
    if not (text.isascii() and text.isdigit()):
        return None
    return int(text)


class NotificationHub:
    """Publishes per-user notifications to open SSE connections."""

    def __init__(self) -> None:
        self.last_issued_id = 0
        self._buffers: dict[str, UserReplayBuffer] = {}
        self._connections: dict[str, list[Connection]] = {}

    def publish(self, user_id: str, payload: dict[str, object]) -> Event:
        """Give the event the next id, buffer it, and push it to open connections."""
        # One counter for all users keeps ids strictly increasing; ids only
        # need to be ordered, not gap-free, within a user's stream.
        self.last_issued_id += 1
        event = Event(self.last_issued_id, user_id, payload)
        self._buffers.setdefault(user_id, UserReplayBuffer()).append(event)
        for connection in self._connections.get(user_id, []):
            connection.frames.append(format_frame(NOTIFICATION_EVENT, event.event_id, payload))
        return event

    def naive_connect(self, user_id: str) -> Connection:
        """Original handler: ignores Last-Event-ID and streams only new events."""
        return self._register(Connection(user_id))

    def connect(self, user_id: str, last_event_id_header: str | None = None) -> Connection:
        """Open a stream that first catches the client up, then goes live."""
        connection = Connection(user_id)
        if last_event_id_header is not None:
            self._catch_up(connection, last_event_id_header)
        return self._register(connection)

    def disconnect(self, connection: Connection) -> None:
        """Stop pushing to a connection that the network dropped."""
        connection.is_open = False
        self._connections[connection.user_id].remove(connection)

    def _register(self, connection: Connection) -> Connection:
        """Add a connection to the user's live list."""
        self._connections.setdefault(connection.user_id, []).append(connection)
        return connection

    def _catch_up(self, connection: Connection, header_value: str) -> None:
        """Replay missed events, or ask for a resync if the gap cannot be filled."""
        last_event_id = parse_last_event_id(header_value)
        buffer = self._buffers.get(connection.user_id, UserReplayBuffer())
        if self._gap_is_lost(last_event_id, buffer):
            connection.frames.append(self._resync_frame())
            return
        for event in buffer.events_after(last_event_id):
            connection.frames.append(format_frame(NOTIFICATION_EVENT, event.event_id, event.payload))

    def _gap_is_lost(self, last_event_id: int | None, buffer: UserReplayBuffer) -> bool:
        """True if we cannot prove the buffer holds every event the client missed."""
        if last_event_id is None:
            return True
        # An id we never issued means our state was lost (restart) or the
        # header is bogus; replaying "nothing" would hide the gap.
        if last_event_id > self.last_issued_id:
            return True
        return last_event_id < buffer.newest_evicted_id

    def _resync_frame(self) -> str:
        """Tell the client to reload; its id lets the next reconnect resume from here."""
        payload = {"reason": "missed events are no longer available"}
        return format_frame(RESYNC_EVENT, self.last_issued_id, payload)
