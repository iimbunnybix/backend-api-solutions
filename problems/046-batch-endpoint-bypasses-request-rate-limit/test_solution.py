import unittest

from solution import (BUCKET_CAPACITY, HTTP_BAD_REQUEST, HTTP_OK, HTTP_TOO_MANY_REQUESTS,
                      MAX_BATCH_SIZE, FakeClock, batch_get, make_bucket, naive_batch_get,
                      simulate_sync_script, single_get)

FULL_BATCH = 100
CALLS_PER_SECOND = 2
ONE_MINUTE = 60
TEN_MINUTES = 600


def ids(count: int) -> list[str]:
    return [f"contact-{index}" for index in range(count)]


def fresh_bucket():
    clock = FakeClock()
    return make_bucket(clock), clock


class ReproduceProblem(unittest.TestCase):
    def test_sync_script_does_120_times_its_quota_in_one_minute(self):
        looked_up = simulate_sync_script(naive_batch_get, FULL_BATCH, CALLS_PER_SECOND, ONE_MINUTE)
        self.assertEqual(looked_up, 12_000)

    def test_a_full_batch_costs_the_same_as_a_single_lookup(self):
        bucket, _ = fresh_bucket()
        naive_batch_get(bucket, ids(FULL_BATCH))
        self.assertEqual(bucket.tokens, BUCKET_CAPACITY - 1)


class FixWithCostBasedCharging(unittest.TestCase):
    def test_sync_script_is_held_to_100_lookups_in_the_first_minute(self):
        looked_up = simulate_sync_script(batch_get, FULL_BATCH, CALLS_PER_SECOND, ONE_MINUTE)
        self.assertEqual(looked_up, 100)

    def test_sustained_rate_matches_quota_over_ten_minutes(self):
        looked_up = simulate_sync_script(batch_get, FULL_BATCH, CALLS_PER_SECOND, TEN_MINUTES)
        self.assertEqual(looked_up, 1_000)

    def test_batch_is_charged_one_token_per_id(self):
        bucket, _ = fresh_bucket()
        batch_get(bucket, ids(30))
        self.assertEqual(bucket.tokens, BUCKET_CAPACITY - 30)

    def test_empty_batch_still_costs_one_token(self):
        bucket, _ = fresh_bucket()
        response = batch_get(bucket, [])
        self.assertEqual(response.status, HTTP_OK)
        self.assertEqual(bucket.tokens, BUCKET_CAPACITY - 1)

    def test_single_and_batch_endpoints_share_the_same_budget(self):
        bucket, _ = fresh_bucket()
        batch_get(bucket, ids(BUCKET_CAPACITY))
        self.assertEqual(single_get(bucket, "contact-1").status, HTTP_TOO_MANY_REQUESTS)

    def test_denied_call_gets_retry_after_that_actually_works(self):
        bucket, clock = fresh_bucket()
        batch_get(bucket, ids(BUCKET_CAPACITY))
        denied = batch_get(bucket, ids(50))
        self.assertEqual(denied.retry_after_seconds, 30)
        clock.advance(denied.retry_after_seconds)
        self.assertEqual(batch_get(bucket, ids(50)).status, HTTP_OK)

    def test_denied_call_is_not_charged(self):
        bucket, _ = fresh_bucket()
        batch_get(bucket, ids(80))
        batch_get(bucket, ids(50))
        self.assertAlmostEqual(bucket.tokens, BUCKET_CAPACITY - 80)

    def test_batch_over_the_size_limit_is_rejected_with_400(self):
        bucket, _ = fresh_bucket()
        response = batch_get(bucket, ids(MAX_BATCH_SIZE + 1))
        self.assertEqual(response.status, HTTP_BAD_REQUEST)
        self.assertEqual(bucket.tokens, BUCKET_CAPACITY)

    def test_batch_bigger_than_bucket_is_rejected_not_retried_forever(self):
        clock = FakeClock()
        small_bucket = make_bucket(clock)
        small_bucket.capacity = 10
        small_bucket.tokens = 10
        response = batch_get(small_bucket, ids(11))
        self.assertEqual(response.status, HTTP_BAD_REQUEST)


if __name__ == "__main__":
    unittest.main()
