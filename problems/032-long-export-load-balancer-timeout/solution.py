"""Turn a slow synchronous CSV export into an asynchronous job.

``GET /reports/orders.csv`` built the whole export inside the HTTP request.
Large merchants' exports took longer than the load balancer's 60-second idle
timeout, so the load balancer returned 504 to the browser while the server
kept working; users clicked "Export" again and every retry started another
full export, multiplying database load.

The fix: ``POST /exports`` only records a job and returns ``202 Accepted`` with
a status URL. A background worker builds the file, and the client polls
``GET /exports/{id}``. An identical request while a job is still pending or
running returns the existing job instead of starting new work.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

LOAD_BALANCER_IDLE_TIMEOUT_SECONDS = 60.0
SECONDS_PER_ROW = 0.0002          # measured cost of reading and formatting one row
POLL_AFTER_SECONDS = 5            # sent as Retry-After so clients do not poll in a tight loop
RESULT_TTL_SECONDS = 24 * 3600    # finished files are kept for one day

PENDING, RUNNING, DONE, EXPIRED = "pending", "running", "done", "expired"


class FakeClock:
    """Deterministic clock in seconds."""

    def __init__(self) -> None:
        self.now_seconds = 0.0

    def now(self) -> float:
        return self.now_seconds

    def advance(self, seconds: float) -> None:
        self.now_seconds += seconds


def export_duration_seconds(row_count: int) -> float:
    """How long building an export of ``row_count`` rows takes."""
    return row_count * SECONDS_PER_ROW


def through_load_balancer(handler_seconds: float) -> int:
    """Status code the client sees for a handler that runs ``handler_seconds``.

    The load balancer gives up after its idle timeout and answers 504, but it
    cannot stop the backend, which keeps working on a response nobody reads.
    """
    if handler_seconds > LOAD_BALANCER_IDLE_TIMEOUT_SECONDS:
        return 504
    return 200


def naive_export(clock: FakeClock, row_count: int) -> tuple[int, float]:
    """Original handler: build the file inside the request.

    Returns (status code seen by the client, backend seconds spent).
    """
    work_seconds = export_duration_seconds(row_count)
    clock.advance(work_seconds)
    return through_load_balancer(work_seconds), work_seconds


@dataclass
class ExportJob:
    """One export request and its progress."""

    job_id: str
    user_id: str
    filters: tuple[tuple[str, str], ...]
    row_count: int
    state: str = PENDING
    seconds_left: float = 0.0
    finished_at: Optional[float] = None


class ExportService:
    """Accepts export requests quickly and builds the files in the background."""

    def __init__(self, clock: FakeClock) -> None:
        self.clock = clock
        self.jobs: dict[str, ExportJob] = {}
        self.backend_seconds = 0.0
        self._next_number = 1

    def submit(self, user_id: str, filters: dict[str, str], row_count: int) -> tuple[int, dict]:
        """Handle ``POST /exports``: answer 202 within milliseconds."""
        # Sort filters so {"a": 1, "b": 2} and {"b": 2, "a": 1} are the same request.
        filter_key = tuple(sorted(filters.items()))
        existing = self._find_active_job(user_id, filter_key)
        if existing is not None:
            return 202, self._accepted_body(existing)
        job = ExportJob(f"exp-{self._next_number}", user_id, filter_key, row_count,
                        seconds_left=export_duration_seconds(row_count))
        self._next_number += 1
        self.jobs[job.job_id] = job
        return 202, self._accepted_body(job)

    def _find_active_job(self, user_id: str, filter_key: tuple) -> Optional[ExportJob]:
        """Return a pending or running job for the same user and filters.

        Repeated clicks must not start duplicate work. Finished jobs are not
        reused, because the user may want fresh data.
        """
        for job in self.jobs.values():
            same_request = job.user_id == user_id and job.filters == filter_key
            if same_request and job.state in (PENDING, RUNNING):
                return job
        return None

    def _accepted_body(self, job: ExportJob) -> dict:
        """Body (and headers) returned with 202 Accepted."""
        return {"job_id": job.job_id, "status_url": f"/exports/{job.job_id}",
                "state": job.state, "retry_after": POLL_AFTER_SECONDS}

    def status(self, user_id: str, job_id: str) -> tuple[int, dict]:
        """Handle ``GET /exports/{id}``."""
        job = self.jobs.get(job_id)
        # Another user's job answers 404, not 403, so job IDs cannot be probed.
        if job is None or job.user_id != user_id:
            return 404, {"error": "not found"}
        self._expire_if_old(job)
        body = {"job_id": job.job_id, "state": job.state}
        if job.state == DONE:
            body["download_url"] = f"/exports/{job.job_id}/file"
        elif job.state == EXPIRED:
            return 410, body
        else:
            body["retry_after"] = POLL_AFTER_SECONDS
        return 200, body

    def _expire_if_old(self, job: ExportJob) -> None:
        """Mark a finished job expired once its file has been deleted."""
        if job.state == DONE and self.clock.now() - job.finished_at >= RESULT_TTL_SECONDS:
            job.state = EXPIRED

    def run_worker(self, seconds: float) -> None:
        """Let the background worker run for ``seconds``, oldest job first."""
        budget = seconds
        for job in self.jobs.values():
            if budget <= 0:
                break
            if job.state not in (PENDING, RUNNING):
                continue
            job.state = RUNNING
            spent = min(budget, job.seconds_left)
            job.seconds_left -= spent
            budget -= spent
            self.backend_seconds += spent
            self.clock.advance(spent)
            if job.seconds_left <= 0:
                job.state = DONE
                job.finished_at = self.clock.now()
