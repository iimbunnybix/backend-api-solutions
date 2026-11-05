import unittest

from solution import (DONE, EXPIRED, LOAD_BALANCER_IDLE_TIMEOUT_SECONDS, PENDING,
                      POLL_AFTER_SECONDS, RESULT_TTL_SECONDS, RUNNING, ExportService,
                      FakeClock, export_duration_seconds, naive_export)

BIG_MERCHANT_ROWS = 500_000      # 100 seconds of work
SMALL_MERCHANT_ROWS = 10_000     # 2 seconds of work
FILTERS = {"from": "2026-10-01", "to": "2026-10-31"}


def finish_all(service: ExportService) -> None:
    """Give the worker more than enough time to finish every queued job."""
    service.run_worker(10_000)


class ReproduceProblem(unittest.TestCase):
    def test_big_export_takes_longer_than_the_load_balancer_waits(self):
        self.assertEqual(export_duration_seconds(BIG_MERCHANT_ROWS), 100.0)
        self.assertGreater(export_duration_seconds(BIG_MERCHANT_ROWS),
                           LOAD_BALANCER_IDLE_TIMEOUT_SECONDS)

    def test_big_export_returns_504_to_the_user(self):
        status, _ = naive_export(FakeClock(), BIG_MERCHANT_ROWS)
        self.assertEqual(status, 504)

    def test_small_export_still_works_which_hid_the_bug(self):
        status, _ = naive_export(FakeClock(), SMALL_MERCHANT_ROWS)
        self.assertEqual(status, 200)

    def test_three_clicks_cost_three_full_exports_and_all_fail(self):
        clock = FakeClock()
        results = [naive_export(clock, BIG_MERCHANT_ROWS) for _ in range(3)]
        self.assertEqual([status for status, _ in results], [504, 504, 504])
        self.assertEqual(sum(seconds for _, seconds in results), 300.0)


class FixAsyncExportJob(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = FakeClock()
        self.service = ExportService(self.clock)

    def test_submit_answers_202_with_a_status_url_without_doing_the_work(self):
        status, body = self.service.submit("merchant-1", FILTERS, BIG_MERCHANT_ROWS)
        self.assertEqual(status, 202)
        self.assertEqual(body["status_url"], "/exports/exp-1")
        self.assertEqual(body["retry_after"], POLL_AFTER_SECONDS)
        self.assertEqual(self.service.backend_seconds, 0.0)

    def test_status_reports_progress_then_download_link(self):
        _, body = self.service.submit("merchant-1", FILTERS, BIG_MERCHANT_ROWS)
        self.assertEqual(self.service.status("merchant-1", body["job_id"])[1]["state"], PENDING)
        self.service.run_worker(40)
        self.assertEqual(self.service.status("merchant-1", body["job_id"])[1]["state"], RUNNING)
        self.service.run_worker(60)
        code, done = self.service.status("merchant-1", body["job_id"])
        self.assertEqual((code, done["state"]), (200, DONE))
        self.assertEqual(done["download_url"], "/exports/exp-1/file")

    def test_repeated_clicks_reuse_the_running_job(self):
        first = self.service.submit("merchant-1", FILTERS, BIG_MERCHANT_ROWS)[1]
        self.service.run_worker(30)
        reordered = {"to": "2026-10-31", "from": "2026-10-01"}
        second = self.service.submit("merchant-1", reordered, BIG_MERCHANT_ROWS)[1]
        self.assertEqual(first["job_id"], second["job_id"])
        finish_all(self.service)
        self.assertEqual(self.service.backend_seconds, 100.0)

    def test_new_request_after_completion_starts_a_fresh_job(self):
        self.service.submit("merchant-1", FILTERS, SMALL_MERCHANT_ROWS)
        finish_all(self.service)
        _, body = self.service.submit("merchant-1", FILTERS, SMALL_MERCHANT_ROWS)
        self.assertEqual(body["job_id"], "exp-2")

    def test_different_users_never_share_a_job(self):
        first = self.service.submit("merchant-1", FILTERS, SMALL_MERCHANT_ROWS)[1]
        second = self.service.submit("merchant-2", FILTERS, SMALL_MERCHANT_ROWS)[1]
        self.assertNotEqual(first["job_id"], second["job_id"])

    def test_other_users_job_is_not_found(self):
        _, body = self.service.submit("merchant-1", FILTERS, SMALL_MERCHANT_ROWS)
        self.assertEqual(self.service.status("merchant-2", body["job_id"])[0], 404)

    def test_unknown_job_is_not_found(self):
        self.assertEqual(self.service.status("merchant-1", "exp-404")[0], 404)

    def test_finished_file_expires_after_its_ttl(self):
        _, body = self.service.submit("merchant-1", FILTERS, SMALL_MERCHANT_ROWS)
        finish_all(self.service)
        self.clock.advance(RESULT_TTL_SECONDS)
        code, expired = self.service.status("merchant-1", body["job_id"])
        self.assertEqual((code, expired["state"]), (410, EXPIRED))

    def test_worker_processes_jobs_oldest_first(self):
        self.service.submit("merchant-1", FILTERS, SMALL_MERCHANT_ROWS)
        self.service.submit("merchant-2", FILTERS, SMALL_MERCHANT_ROWS)
        self.service.run_worker(2)
        self.assertEqual(self.service.jobs["exp-1"].state, DONE)
        self.assertEqual(self.service.jobs["exp-2"].state, PENDING)


if __name__ == "__main__":
    unittest.main()
