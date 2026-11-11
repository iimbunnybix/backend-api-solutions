"""Priority job queue that no longer starves low-priority jobs.

The worker always ran the highest-priority job first. During a sustained burst
of high-priority work (order webhooks) the queue never ran dry, so low-priority
jobs (invoice emails) waited forever. This module keeps the original strict
picker for comparison and adds an aging picker: every AGING_SECONDS a job has
waited promotes it one priority level, so every job eventually competes as
HIGH and is then served in arrival order. That gives each job a bounded wait.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from typing import Callable


class Priority(IntEnum):
    """Lower number = served first."""

    HIGH = 0     # order webhooks, payment callbacks
    NORMAL = 1   # search re-indexing
    LOW = 2      # invoice emails, report generation


# How long a job must wait to be promoted one priority level.
AGING_SECONDS = 30
# Every job in the simulation takes one second of worker time.
SERVICE_SECONDS = 1


@dataclass(frozen=True)
class Job:
    job_id: str
    priority: Priority
    enqueued_at: int


Picker = Callable[[list[Job], int], Job]


def naive_pick(waiting: list[Job], now: int) -> Job:
    """Original picker: highest priority first, oldest first within a priority."""
    best = waiting[0]
    for job in waiting[1:]:
        if (job.priority, job.enqueued_at) < (best.priority, best.enqueued_at):
            best = job
    return best


def effective_priority(job: Job, now: int, aging_seconds: int = AGING_SECONDS) -> int:
    """Priority after aging: one level better per full aging interval waited."""
    levels_gained = (now - job.enqueued_at) // aging_seconds
    # Never better than HIGH, so promoted jobs queue fairly behind older HIGH jobs.
    return max(int(Priority.HIGH), int(job.priority) - levels_gained)


def aging_pick(waiting: list[Job], now: int, aging_seconds: int = AGING_SECONDS) -> Job:
    """Fixed picker: best effective priority first, oldest first on a tie."""
    best = waiting[0]
    best_rank = (effective_priority(best, now, aging_seconds), best.enqueued_at)
    for job in waiting[1:]:
        rank = (effective_priority(job, now, aging_seconds), job.enqueued_at)
        if rank < best_rank:
            best, best_rank = job, rank
    return best


def max_wait_bound(priority: Priority, aging_seconds: int = AGING_SECONDS) -> int:
    """Seconds until a job of this priority competes as HIGH.

    After that point it is served in arrival order among HIGH jobs, so as long
    as the worker keeps up with the HIGH arrival rate it starts soon after.
    """
    return int(priority) * aging_seconds


def simulate(pick: Picker, arrivals: list[Job], duration: int) -> dict[str, int]:
    """Run one worker for ``duration`` seconds; return job_id -> start second.

    Jobs that never started are missing from the result.
    """
    pending = sorted(arrivals, key=job_arrival_time)
    waiting: list[Job] = []
    started: dict[str, int] = {}
    next_arrival = 0
    for now in range(0, duration, SERVICE_SECONDS):
        # Move every job that has arrived by now into the waiting list.
        while next_arrival < len(pending) and pending[next_arrival].enqueued_at <= now:
            waiting.append(pending[next_arrival])
            next_arrival += 1
        if not waiting:
            continue
        job = pick(waiting, now)
        waiting.remove(job)
        started[job.job_id] = now
    return started


def job_arrival_time(job: Job) -> int:
    """Sort key: when the job entered the queue."""
    return job.enqueued_at


def sustained_load(duration: int, low_jobs: int) -> list[Job]:
    """One HIGH job per second (the worker's full capacity) plus LOW jobs at t=0."""
    jobs = []
    for second in range(duration):
        jobs.append(Job(f"webhook-{second}", Priority.HIGH, second))
    for index in range(low_jobs):
        jobs.append(Job(f"invoice-{index}", Priority.LOW, 0))
    return jobs


def waits_for(started: dict[str, int], jobs: list[Job], priority: Priority) -> list[int]:
    """Wait in seconds of every started job with the given priority."""
    waits = []
    for job in jobs:
        if job.priority == priority and job.job_id in started:
            waits.append(started[job.job_id] - job.enqueued_at)
    return waits


if __name__ == "__main__":
    jobs = sustained_load(duration=600, low_jobs=5)
    for name, picker in (("strict", naive_pick), ("aging", aging_pick)):
        started = simulate(picker, jobs, duration=600)
        low = waits_for(started, jobs, Priority.LOW)
        high = waits_for(started, jobs, Priority.HIGH)
        print(name, "low started:", len(low), "low waits:", low,
              "max high wait:", max(high), "high started:", len(high))
