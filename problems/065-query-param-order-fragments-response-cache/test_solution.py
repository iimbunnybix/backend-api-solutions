import random
import unittest

from solution import (
    ResponseCache,
    canonical_cache_key,
    canonical_params,
    naive_cache_key,
    replay_traffic,
)

PATH = "/products"
TRAFFIC_SEED = 65
REQUEST_COUNT = 10_000
PAGES = range(1, 6)
SORTS = ["price", "-price", "rating", "newest"]
UTM_SOURCES = [None, "newsletter", "instagram", "google", "partner"]
BRAND_SPELLINGS = ["acme%2Czen", "acme%2czen", "acme,zen"]


def random_listing_query(rng):
    """One request for a listing page, written the way different clients do."""
    params = [f"page={rng.choice(PAGES)}", f"sort={rng.choice(SORTS)}",
              f"brand={rng.choice(BRAND_SPELLINGS)}"]
    utm_source = rng.choice(UTM_SOURCES)
    if utm_source is not None:
        params.append(f"utm_source={utm_source}")
    rng.shuffle(params)
    query = "&".join(params)
    if rng.random() < 0.2:
        query += "&"
    return query


def realistic_traffic():
    """Seeded traffic: 20 distinct listings requested in many spellings."""
    rng = random.Random(TRAFFIC_SEED)
    return [random_listing_query(rng) for _ in range(REQUEST_COUNT)]


SAME_LISTING_SPELLINGS = [
    "page=2&sort=price",
    "sort=price&page=2",
    "page=2&sort=price&",
    "page=2&&sort=price",
    "utm_source=newsletter&page=2&sort=price",
    "page=2&sort=price&utm_campaign=bf&gclid=abc123",
    "%70age=2&sort=price",
]


class ReproduceProblem(unittest.TestCase):
    def test_reordered_parameters_get_different_naive_keys(self):
        self.assertNotEqual(naive_cache_key(PATH, "page=2&sort=price"),
                            naive_cache_key(PATH, "sort=price&page=2"))

    def test_every_spelling_of_one_listing_hits_the_database(self):
        cache = replay_traffic(naive_cache_key, PATH, SAME_LISTING_SPELLINGS)
        self.assertEqual(cache.backend_calls, len(SAME_LISTING_SPELLINGS))

    def test_realistic_traffic_has_a_low_hit_rate(self):
        cache = replay_traffic(naive_cache_key, PATH, realistic_traffic())
        self.assertGreater(cache.backend_calls, 100 * len(PAGES) * len(SORTS))
        self.assertLess(cache.hit_rate(), 0.5)


class FixCanonicalKeyMergesEquivalentQueries(unittest.TestCase):
    def test_parameter_order_does_not_change_the_key(self):
        self.assertEqual(canonical_cache_key(PATH, "page=2&sort=price"),
                         canonical_cache_key(PATH, "sort=price&page=2"))

    def test_all_spellings_of_one_listing_share_one_entry(self):
        cache = replay_traffic(canonical_cache_key, PATH, SAME_LISTING_SPELLINGS)
        self.assertEqual(cache.backend_calls, 1)

    def test_percent_encoding_case_and_literal_comma_are_equal(self):
        keys = {canonical_cache_key(PATH, f"brand={spelling}") for spelling in BRAND_SPELLINGS}
        self.assertEqual(keys, {"/products?brand=acme%2Czen"})

    def test_plus_and_percent_20_both_mean_space(self):
        self.assertEqual(canonical_cache_key(PATH, "q=red+kettle"),
                         canonical_cache_key(PATH, "q=red%20kettle"))

    def test_tracking_parameters_are_dropped(self):
        key = canonical_cache_key(PATH, "utm_source=x&utm_medium=y&fbclid=z&page=1")
        self.assertEqual(key, "/products?page=1")

    def test_realistic_traffic_needs_one_database_call_per_listing(self):
        cache = replay_traffic(canonical_cache_key, PATH, realistic_traffic())
        self.assertEqual(cache.backend_calls, len(PAGES) * len(SORTS))


class FixKeepsDifferentQueriesApart(unittest.TestCase):
    def test_different_values_get_different_keys(self):
        self.assertNotEqual(canonical_cache_key(PATH, "page=2"),
                            canonical_cache_key(PATH, "page=3"))

    def test_repeated_values_keep_their_relative_order(self):
        self.assertEqual(canonical_params("sort=rating&page=1&sort=price"),
                         [("page", "1"), ("sort", "rating"), ("sort", "price")])

    def test_swapping_repeated_values_changes_the_key(self):
        self.assertNotEqual(canonical_cache_key(PATH, "sort=price&sort=rating"),
                            canonical_cache_key(PATH, "sort=rating&sort=price"))

    def test_blank_value_is_kept_distinct_from_a_missing_parameter(self):
        self.assertNotEqual(canonical_cache_key(PATH, "brand="),
                            canonical_cache_key(PATH, ""))

    def test_encoded_ampersand_in_a_value_stays_part_of_the_value(self):
        self.assertNotEqual(canonical_cache_key(PATH, "brand=a%26b"),
                            canonical_cache_key(PATH, "brand=a&b"))

    def test_different_paths_get_different_keys(self):
        self.assertNotEqual(canonical_cache_key("/products", "page=1"),
                            canonical_cache_key("/orders", "page=1"))

    def test_parameter_names_that_only_contain_utm_are_kept(self):
        self.assertEqual(canonical_cache_key(PATH, "no_utm_here=1"), "/products?no_utm_here=1")


class FixResponseCacheCounters(unittest.TestCase):
    def test_empty_cache_reports_zero_hit_rate(self):
        self.assertEqual(ResponseCache(canonical_cache_key).hit_rate(), 0.0)

    def test_second_identical_request_is_a_hit(self):
        cache = replay_traffic(canonical_cache_key, PATH, ["page=1", "page=1"])
        self.assertEqual((cache.hits, cache.backend_calls), (1, 1))


if __name__ == "__main__":
    unittest.main()
