import unittest

from solution import (AGING_SECONDS, Job, Priority, aging_pick, effective_priority,
                      max_wait_bound, naive_pick, simulate, sustained_load, waits_for)

DURATION = 600
LOW_JOBS = 5


def run(picker):
    """Simulate ten minutes of saturating HIGH load with five LOW jobs queued at t=0."""
    jobs = sustained_load(DURATION, LOW_JOBS)
    return jobs, simulate(picker, jobs, DURATION)


class ReproduceProblem(unittest.TestCase):
    def test_low_priority_jobs_never_run_under_sustained_high_load(self):
        jobs, started = run(naive_pick)
        self.assertEqual(waits_for(started, jobs, Priority.LOW), [])

    def test_every_high_job_runs_immediately_while_low_jobs_starve(self):
        jobs, started = run(naive_pick)
        self.assertEqual(max(waits_for(started, jobs, Priority.HIGH)), 0)


class FixWithAging(unittest.TestCase):
    def test_every_low_job_runs_under_sustained_high_load(self):
        jobs, started = run(aging_pick)
        self.assertEqual(len(waits_for(started, jobs, Priority.LOW)), LOW_JOBS)

    def test_low_jobs_start_soon_after_reaching_high_priority(self):
        jobs, started = run(aging_pick)
        bound = max_wait_bound(Priority.LOW)
        for wait in waits_for(started, jobs, Priority.LOW):
            self.assertGreaterEqual(wait, bound)
            self.assertLessEqual(wait, bound + LOW_JOBS)

    def test_high_jobs_are_delayed_only_by_the_promoted_jobs(self):
        jobs, started = run(aging_pick)
        self.assertEqual(max(waits_for(started, jobs, Priority.HIGH)), LOW_JOBS)

    def test_high_job_still_goes_first_when_nobody_has_waited(self):
        waiting = [Job("invoice", Priority.LOW, 0), Job("webhook", Priority.HIGH, 0)]
        self.assertEqual(aging_pick(waiting, now=0).job_id, "webhook")

    def test_jobs_of_the_same_priority_run_in_arrival_order(self):
        waiting = [Job("second", Priority.NORMAL, 5), Job("first", Priority.NORMAL, 2)]
        self.assertEqual(aging_pick(waiting, now=6).job_id, "first")

    def test_one_aging_interval_promotes_exactly_one_level(self):
        job = Job("invoice", Priority.LOW, 0)
        self.assertEqual(effective_priority(job, AGING_SECONDS - 1), Priority.LOW)
        self.assertEqual(effective_priority(job, AGING_SECONDS), Priority.NORMAL)

    def test_aging_never_goes_above_high(self):
        job = Job("invoice", Priority.LOW, 0)
        self.assertEqual(effective_priority(job, 10 * AGING_SECONDS), Priority.HIGH)

    def test_promoted_job_does_not_jump_ahead_of_an_older_high_job(self):
        waiting = [Job("old-webhook", Priority.HIGH, 0), Job("invoice", Priority.LOW, 1)]
        now = 1 + 2 * AGING_SECONDS
        self.assertEqual(aging_pick(waiting, now).job_id, "old-webhook")

    def test_idle_worker_starts_nothing(self):
        self.assertEqual(simulate(aging_pick, [], duration=10), {})


if __name__ == "__main__":
    unittest.main()
