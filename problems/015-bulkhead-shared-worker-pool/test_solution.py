import unittest

from solution import (ACCEPTED, REJECTED_BULKHEAD_FULL, REJECTED_POOL_FULL,
                      BulkheadServer, NaiveSharedPoolServer, Request, run_traffic)

POOL_SIZE = 40
RECOMMENDATIONS_LIMIT = 8
TEST_SECONDS = 60
SLOW_RECOMMENDATIONS = {"recommendations": (5, 30.0), "payments": (10, 0.5)}
HEALTHY_RECOMMENDATIONS = {"recommendations": (5, 0.05), "payments": (10, 0.5)}


def make_bulkhead_server() -> BulkheadServer:
    return BulkheadServer(POOL_SIZE, {"recommendations": RECOMMENDATIONS_LIMIT})


class ReproduceProblem(unittest.TestCase):
    def test_slow_dependency_starves_payments_in_shared_pool(self):
        outcomes = run_traffic(NaiveSharedPoolServer(POOL_SIZE), SLOW_RECOMMENDATIONS, TEST_SECONDS)
        self.assertEqual(outcomes["payments"][ACCEPTED], 65)
        self.assertEqual(outcomes["payments"][REJECTED_POOL_FULL], 535)

    def test_shared_pool_is_fine_while_dependency_is_healthy(self):
        outcomes = run_traffic(NaiveSharedPoolServer(POOL_SIZE), HEALTHY_RECOMMENDATIONS, TEST_SECONDS)
        self.assertEqual(outcomes["payments"], {ACCEPTED: 600})


class FixWithBulkheads(unittest.TestCase):
    def test_payments_keep_full_capacity_while_recommendations_are_slow(self):
        outcomes = run_traffic(make_bulkhead_server(), SLOW_RECOMMENDATIONS, TEST_SECONDS)
        self.assertEqual(outcomes["payments"], {ACCEPTED: 600})

    def test_slow_dependency_is_rejected_by_its_bulkhead_not_the_pool(self):
        outcomes = run_traffic(make_bulkhead_server(), SLOW_RECOMMENDATIONS, TEST_SECONDS)
        self.assertNotIn(REJECTED_POOL_FULL, outcomes["recommendations"])
        self.assertEqual(outcomes["recommendations"][ACCEPTED], 16)

    def test_slow_dependency_never_holds_more_than_its_limit(self):
        server = make_bulkhead_server()
        for _ in range(20):
            server.handle(Request("recommendations", 30.0), now=0.0)
        self.assertEqual(len(server.pool.busy_until), RECOMMENDATIONS_LIMIT)

    def test_compartment_frees_up_when_requests_finish(self):
        server = make_bulkhead_server()
        for _ in range(RECOMMENDATIONS_LIMIT):
            server.handle(Request("recommendations", 30.0), now=0.0)
        self.assertEqual(server.handle(Request("recommendations", 30.0), now=29.0),
                         REJECTED_BULKHEAD_FULL)
        self.assertEqual(server.handle(Request("recommendations", 30.0), now=30.0), ACCEPTED)

    def test_dependency_without_limit_still_respects_the_pool(self):
        server = BulkheadServer(pool_size=2, bulkhead_limits={})
        server.handle(Request("payments", 10.0), now=0.0)
        server.handle(Request("payments", 10.0), now=0.0)
        self.assertEqual(server.handle(Request("payments", 10.0), now=0.0), REJECTED_POOL_FULL)

    def test_healthy_traffic_is_unaffected_by_bulkheads(self):
        outcomes = run_traffic(make_bulkhead_server(), HEALTHY_RECOMMENDATIONS, TEST_SECONDS)
        self.assertEqual(outcomes["recommendations"], {ACCEPTED: 300})
        self.assertEqual(outcomes["payments"], {ACCEPTED: 600})


if __name__ == "__main__":
    unittest.main()
