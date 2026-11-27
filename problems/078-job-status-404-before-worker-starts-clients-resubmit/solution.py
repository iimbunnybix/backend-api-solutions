"""Job status returned 404 until a worker started the job, so clients resubmitted.

``POST /exports`` answered ``202 Accepted`` with a job id, but the job record
was only written when a worker took the message off the queue. While the
queue was backlogged, ``GET /exports/{id}`` returned 404, the client SDK
treated 404 as "job lost" and submitted again, and each resubmission became
another billed export. The fix writes a PENDING record before answering 202
(with a ``Location`` header), the worker moves it PENDING -> RUNNING -> DONE,
and the status endpoint never returns 404 for an id it has accepted.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

PENDING, RUNNING, DONE, FAILED = "pending", "running", "done", "failed"
ALLOWED_TRANSITIONS = {PENDING: RUNNING, RUNNING: DONE}

PEAK_QUEUE_BACKLOG_SECONDS = 9    # how long a message waits at peak before a worker takes it
QUIET_QUEUE_BACKLOG_SECONDS = 1   # the same wait off-peak
EXPORT_DURATION_SECONDS = 4       # how long building one export takes
POLL_INTERVAL_SECONDS = 2         # the client SDK's polling interval
CLIENT_DEADLINE_SECONDS = 60      # the SDK gives up waiting after this long


@dataclass
class FakeClock:
    now: int = 0

    def advance(self, seconds: int = 1) -> None:
        """Move time forward; nothing in this model reads the real clock."""
        self.now += seconds


@dataclass
class FakeQueue:
    """A FIFO queue. ``fail_next_publish`` simulates the broker being down."""

    messages: list[tuple[int, str]] = field(default_factory=list)   # (enqueued_at, job_id)
    fail_next_publish: bool = False

    def publish(self, job_id: str, now: int) -> None:
        """Add a message, or raise if the broker is unavailable."""
        if self.fail_next_publish:
            self.fail_next_publish = False
            raise ConnectionError("queue unavailable")
        self.messages.append((now, job_id))


@dataclass
class Job:
    job_id: str
    status: str
    status_changed_at: int


@dataclass(frozen=True)
class HttpResponse:
    status: int
    headers: dict[str, str] = field(default_factory=dict)
    body: dict = field(default_factory=dict)


@dataclass
class JobSystem:
    """API, job table, queue and worker state for one customer."""

    clock: FakeClock = field(default_factory=FakeClock)
    queue: FakeQueue = field(default_factory=FakeQueue)
    jobs: dict[str, Job] = field(default_factory=dict)
    next_number: int = 1
    exports_built: int = 0            # each one is billed to the customer
    queue_backlog_seconds: int = PEAK_QUEUE_BACKLOG_SECONDS

    def new_job_id(self) -> str:
        """Sequential ids keep the tests deterministic."""
        job_id = f"exp_{self.next_number:04d}"
        self.next_number += 1
        return job_id


def accepted(job_id: str) -> HttpResponse:
    """The 202 answer: the id plus where to poll for it."""
    return HttpResponse(202, {"Location": f"/exports/{job_id}"}, {"job_id": job_id})


def set_status(job: Job, new_status: str, now: int) -> None:
    """Move a job forward one step; refuse anything out of order."""
    if ALLOWED_TRANSITIONS.get(job.status) != new_status:
        raise ValueError(f"{job.job_id}: cannot go from {job.status} to {new_status}")
    job.status = new_status
    job.status_changed_at = now


# ---------------------------------------------------------------- original
def original_submit_export(system: JobSystem) -> HttpResponse:
    """Original API: enqueue and answer 202. No job record exists yet."""
    job_id = system.new_job_id()
    system.queue.publish(job_id, system.clock.now)
    return accepted(job_id)


def original_pick_up(system: JobSystem, job_id: str) -> None:
    """Original worker: the job record is first created here, as RUNNING."""
    system.jobs[job_id] = Job(job_id, RUNNING, system.clock.now)


# ---------------------------------------------------------------- the fix
def submit_export(system: JobSystem) -> HttpResponse:
    """Write a PENDING record, then enqueue, then answer 202.

    The record exists before the client ever learns the id, so no poll can
    race ahead of it. If the queue is down we must not answer 202 for a job
    that will never run: mark it FAILED and tell the client to retry later.
    """
    job_id = system.new_job_id()
    system.jobs[job_id] = Job(job_id, PENDING, system.clock.now)
    try:
        system.queue.publish(job_id, system.clock.now)
    except ConnectionError:
        # FAILED is a terminal state reachable from PENDING only, here.
        system.jobs[job_id].status = FAILED
        return HttpResponse(503, {"Retry-After": "5"}, {"job_id": job_id, "status": FAILED})
    return accepted(job_id)


def pick_up(system: JobSystem, job_id: str) -> None:
    """Fixed worker: claim a PENDING job by moving it to RUNNING.

    Queues deliver at least once, so the same message can arrive twice. Only
    a PENDING job is claimed; a redelivery for a RUNNING or DONE job is
    ignored instead of building (and billing) the export again.
    """
    job = system.jobs.get(job_id)
    if job is None or job.status != PENDING:
        return
    set_status(job, RUNNING, system.clock.now)


# ---------------------------------------------------------------- shared
def get_status(system: JobSystem, job_id: str) -> HttpResponse:
    """``GET /exports/{id}``: 404 only if the id was never accepted."""
    job = system.jobs.get(job_id)
    if job is None:
        return HttpResponse(404, {}, {"error": "not_found"})
    return HttpResponse(200, {}, {"job_id": job_id, "status": job.status})


PickUp = Callable[[JobSystem, str], None]
Submit = Callable[[JobSystem], HttpResponse]


def next_message_is_due(system: JobSystem) -> bool:
    """True if the oldest message has waited out the queue backlog."""
    if not system.queue.messages:
        return False
    enqueued_at, _ = system.queue.messages[0]
    return system.clock.now - enqueued_at >= system.queue_backlog_seconds


def worker_tick(system: JobSystem, pick_up_job: PickUp) -> None:
    """One second of worker time: finish due jobs, then take due messages."""
    now = system.clock.now
    for job in system.jobs.values():
        if job.status == RUNNING and now - job.status_changed_at >= EXPORT_DURATION_SECONDS:
            set_status(job, DONE, now)
            system.exports_built += 1
    # The backlog means a message only reaches a worker after a delay.
    while next_message_is_due(system):
        _, job_id = system.queue.messages.pop(0)
        pick_up_job(system, job_id)


def run_sdk_export(system: JobSystem, submit: Submit, pick_up_job: PickUp) -> int:
    """Run the customer's SDK against the API; return how many times it submitted.

    The SDK polls every few seconds and, like the real one, treats 404 as
    "the job was lost" and submits a new export. That SDK is already in
    customers' hands, so the fix has to be on the server.
    """
    submissions = 1
    job_id = submit(system).body["job_id"]
    while system.clock.now < CLIENT_DEADLINE_SECONDS:
        system.clock.advance()
        worker_tick(system, pick_up_job)
        if system.clock.now % POLL_INTERVAL_SECONDS != 0:
            continue
        response = get_status(system, job_id)
        if response.status == 404:
            submissions += 1
            job_id = submit(system).body["job_id"]
        elif response.body["status"] == DONE:
            break
    return submissions


def drain(system: JobSystem, pick_up_job: PickUp) -> None:
    """Let the workers finish everything still queued or running."""
    while system.queue.messages or any(j.status == RUNNING for j in system.jobs.values()):
        system.clock.advance()
        worker_tick(system, pick_up_job)
