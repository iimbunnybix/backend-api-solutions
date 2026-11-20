# 066 · Customers on mobile data never saw "your order has shipped" in the app, although the notification service logged every event as sent

## Problem
A delivery app shows live order updates ("confirmed", "shipped", "driver
nearby", "delivered") through a Server-Sent Events stream,
`GET /notifications/stream`. The web app uses the browser's `EventSource`,
the mobile apps use an SSE client library; both reconnect automatically
when the connection drops.

Mobile connections drop constantly: switching between Wi-Fi and mobile
data, entering a lift, the OS suspending the app, a load balancer closing
idle connections. Support received "the app still says 'preparing' but the
driver is at my door" tickets every day, nearly all from mobile users.
The notification service logs showed every event published, and the
stream reconnected within seconds, so nobody suspected the stream.

## Root cause
The stream handler subscribed the new connection to events published
**from now on** (`naive_connect`). Events never carried an `id:` field and
the server kept no history, so an update published during the few seconds
between the drop and the reconnect went to no connection at all.
`ReproduceProblem` disconnects after "order confirmed", publishes "order
shipped" and "driver nearby", reconnects and publishes "delivered": the
client receives only the first and last.

SSE already has the mechanism for this: when a message carries an
`id:` line, the client remembers it and sends it as the `Last-Event-ID`
request header when it reconnects. The server simply never used it.

## Solution
- **Every event gets an id.** `NotificationHub.publish` assigns ids from
  one counter, so they strictly increase, and writes them as the SSE `id:`
  field (`format_frame`).
- **Bounded replay buffer per user.** Each user keeps their newest
  `REPLAY_BUFFER_SIZE` (100) events in a `deque`, plus the id of the newest
  event that was evicted. One user's traffic cannot evict another user's
  events.
- **Resume from `Last-Event-ID`.** `connect` replays the buffered events
  with a larger id, oldest first, and then goes live. Events the client
  already saw are not sent again.
- **Say so when the gap cannot be filled.** If the client's id is older
  than the newest evicted event, larger than any id the server issued
  (state lost in a restart) or not a number, the server sends one
  `event: resync` message instead of silently skipping. The app reloads
  its orders over the normal REST API. The resync message carries the
  latest id, so the next reconnect resumes from there.

The tests show missed events replayed in order, no duplicates, no events
from other users, a gap of exactly 100 events still replayed, a gap of 101
answered with a resync, and a resume working after a resync.

## Trade-offs
- The buffer lives in one process's memory. With several stream servers,
  or across deploys, it has to be shared: a Redis Stream per user (its
  entry ids work as SSE ids), Kafka offsets, or an events table with an
  auto-increment id. The id counter must also survive restarts, or ids
  start again from 1 and look "old"; rejecting ids the server never issued
  catches that case.
- Buffer size is a memory-versus-resync trade: 100 events per user is
  cheap, but a user offline for a day will always get a resync. Expiring
  by age as well as count keeps idle users from holding memory forever.
- Replay gives at-least-once delivery across tabs and retries, so clients
  should treat events as idempotent ("order 42 is shipped"), not as
  increments.
- Push notifications (APNs, FCM) are still needed for apps that are fully
  closed; SSE only covers the time the app is open.

## Run
```bash
python -m unittest -v test_solution.py
```
