import unittest

from solution import (CLIENT_DEADLINE_SECONDS, DONE, FAILED, PENDING, QUIET_QUEUE_BACKLOG_SECONDS,
                      RUNNING, JobSystem, drain, get_status, original_pick_up,
                      original_submit_export, pick_up, run_sdk_export, set_status, submit_export,
                      worker_tick)


def run_original(system: JobSystem) -> int:
    """Run the SDK against the original API and worker, then drain the queue."""
    submissions = run_sdk_export(system, original_submit_export, original_pick_up)
    drain(system, original_pick_up)
    return submissions


def run_fixed(system: JobSystem) -> int:
    """Run the SDK against the fixed API and worker, then drain the queue."""
    submissions = run_sdk_export(system, submit_export, pick_up)
    drain(system, pick_up)
    return submissions


def advance(system: JobSystem, seconds: int) -> None:
    """Let the fixed worker run for ``seconds`` of fake time."""
    for _ in range(seconds):
        system.clock.advance()
        worker_tick(system, pick_up)


class ReproduceProblem(unittest.TestCase):
    def test_status_is_404_right_after_the_202(self):
        system = JobSystem()
        job_id = original_submit_export(system).body["job_id"]
        self.assertEqual(get_status(system, job_id).status, 404)

    def test_backlogged_queue_makes_one_click_build_many_exports(self):
        system = JobSystem()
        submissions = run_original(system)
        self.assertEqual(submissions, 31)
        self.assertEqual(system.exports_built, 31)

    def test_sdk_never_gets_its_file_before_the_deadline(self):
        system = JobSystem()
        submissions = run_sdk_export(system, original_submit_export, original_pick_up)
        last_job = f"exp_{submissions:04d}"
        self.assertEqual(system.clock.now, CLIENT_DEADLINE_SECONDS)
        self.assertEqual(get_status(system, last_job).status, 404)

    def test_quiet_queue_hides_the_bug(self):
        system = JobSystem(queue_backlog_seconds=QUIET_QUEUE_BACKLOG_SECONDS)
        self.assertEqual(run_original(system), 1)


class FixPendingRecordAtEnqueue(unittest.TestCase):
    def test_status_is_pending_right_after_the_202(self):
        system = JobSystem()
        job_id = submit_export(system).body["job_id"]
        self.assertEqual(get_status(system, job_id).body["status"], PENDING)

    def test_202_has_a_location_header_for_the_job(self):
        response = submit_export(JobSystem())
        self.assertEqual(response.status, 202)
        self.assertEqual(response.headers["Location"], "/exports/exp_0001")

    def test_one_click_builds_one_export_even_at_peak(self):
        system = JobSystem()
        self.assertEqual(run_fixed(system), 1)
        self.assertEqual(system.exports_built, 1)

    def test_job_goes_pending_then_running_then_done(self):
        system = JobSystem()
        job_id = submit_export(system).body["job_id"]
        seen = [get_status(system, job_id).body["status"]]
        for _ in range(20):
            advance(system, 1)
            status = get_status(system, job_id).body["status"]
            if status != seen[-1]:
                seen.append(status)
        self.assertEqual(seen, [PENDING, RUNNING, DONE])

    def test_unknown_job_id_is_still_404(self):
        self.assertEqual(get_status(JobSystem(), "exp_9999").status, 404)

    def test_redelivered_message_does_not_build_the_export_twice(self):
        system = JobSystem()
        job_id = submit_export(system).body["job_id"]
        # At-least-once delivery: the broker hands the same message over again.
        system.queue.publish(job_id, system.clock.now)
        drain(system, pick_up)
        self.assertEqual(system.exports_built, 1)

    def test_queue_outage_returns_503_instead_of_an_orphan_202(self):
        system = JobSystem()
        system.queue.fail_next_publish = True
        response = submit_export(system)
        self.assertEqual(response.status, 503)
        self.assertEqual(get_status(system, response.body["job_id"]).body["status"], FAILED)

    def test_out_of_order_transition_is_refused(self):
        system = JobSystem()
        job_id = submit_export(system).body["job_id"]
        with self.assertRaises(ValueError):
            set_status(system.jobs[job_id], DONE, system.clock.now)


if __name__ == "__main__":
    unittest.main()
