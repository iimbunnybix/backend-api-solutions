import random
import unittest

from solution import (
    BASE_TTL_SECONDS,
    FakeClock,
    TtlCache,
    make_jittered_ttl,
    naive_ttl,
    simulate_hot_keys,
)

KEY_COUNT = 1000
# Two full TTL periods, so we also see whether the spike repeats.
DURATION_SECONDS = 2 * int(BASE_TTL_SECONDS) + 60


def seeded_jitter(seed: int = 7, fraction: float = 0.10):
    return make_jittered_ttl(random.Random(seed), fraction)


class ReproduceProblem(unittest.TestCase):
    def test_every_key_reloads_in_the_same_second(self):
        database = simulate_hot_keys(naive_ttl, KEY_COUNT, DURATION_SECONDS)
        self.assertEqual(database.loads_per_second[300], KEY_COUNT)
        self.assertEqual(database.peak_loads_in_one_second(), KEY_COUNT)

    def test_the_spike_repeats_every_ttl_period(self):
        database = simulate_hot_keys(naive_ttl, KEY_COUNT, DURATION_SECONDS)
        self.assertEqual(sorted(database.loads_per_second), [300, 600])


class FixJitteredTtl(unittest.TestCase):
    def test_peak_load_drops_by_more_than_ten_times(self):
        database = simulate_hot_keys(seeded_jitter(), KEY_COUNT, DURATION_SECONDS)
        self.assertLess(database.peak_loads_in_one_second(), KEY_COUNT // 10)

    def test_total_loads_in_first_period_are_unchanged(self):
        database = simulate_hot_keys(seeded_jitter(), KEY_COUNT, 335)
        self.assertEqual(sum(database.loads_per_second.values()), KEY_COUNT)

    def test_first_reloads_fall_inside_the_jitter_window(self):
        database = simulate_hot_keys(seeded_jitter(), KEY_COUNT, 335)
        seconds = list(database.loads_per_second)
        self.assertGreaterEqual(min(seconds), 270)
        self.assertLessEqual(max(seconds), 331)

    def test_jittered_ttls_stay_within_bounds(self):
        policy = seeded_jitter()
        for _ in range(10_000):
            ttl = policy(300.0)
            self.assertGreaterEqual(ttl, 270.0)
            self.assertLessEqual(ttl, 330.0)

    def test_tiny_base_ttl_never_goes_below_minimum(self):
        policy = seeded_jitter(fraction=0.9)
        self.assertGreaterEqual(min(policy(1.0) for _ in range(1000)), 1.0)

    def test_zero_jitter_behaves_like_the_original(self):
        self.assertEqual(seeded_jitter(fraction=0.0)(300.0), 300.0)

    def test_invalid_jitter_fraction_is_rejected(self):
        for bad_fraction in (-0.1, 1.0, 2.0):
            with self.assertRaises(ValueError):
                make_jittered_ttl(random.Random(1), bad_fraction)


class FixCacheBehaviour(unittest.TestCase):
    def test_value_is_served_from_cache_before_expiry(self):
        clock = FakeClock()
        cache = TtlCache(clock, naive_ttl, base_ttl_seconds=10)
        loads = []
        cache.get_or_load("a", lambda key: loads.append(key) or "v1")
        clock.advance(9.9)
        self.assertEqual(cache.get_or_load("a", lambda key: "v2"), "v1")
        self.assertEqual(loads, ["a"])

    def test_value_is_reloaded_at_expiry(self):
        clock = FakeClock()
        cache = TtlCache(clock, naive_ttl, base_ttl_seconds=10)
        cache.put("a", "old")
        clock.advance(10)
        self.assertEqual(cache.get_or_load("a", lambda key: "new"), "new")


if __name__ == "__main__":
    unittest.main()
