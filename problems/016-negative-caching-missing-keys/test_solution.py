import unittest

from solution import (NOT_FOUND, NOT_FOUND_TTL_SECONDS, FakeClock, ProductDatabase,
                      TTLCache, create_product, get_product, naive_get_product,
                      simulate_scraper)

MUG = {"name": "Mug", "price_cents": 1200}
MISSING_IDS = [f"sku-missing-{index}" for index in range(500)]
SCRAPER_ROUNDS = 20
SECONDS_BETWEEN_ROUNDS = 6.0


class DatabaseWithConcurrentCreate(ProductDatabase):
    """Fake database where a writer creates the product right after a lookup.

    It models a reader that has queried the database (and seen nothing) but
    has not yet written NOT_FOUND to the cache when the create commits and
    invalidates the key.
    """

    def __init__(self, cache: TTLCache, product_id: str, product: dict) -> None:
        super().__init__({})
        self.cache = cache
        self.pending_create: tuple[str, dict] | None = (product_id, product)

    def find_product(self, product_id: str) -> dict | None:
        result = super().find_product(product_id)
        if self.pending_create is not None:
            new_id, new_product = self.pending_create
            self.pending_create = None
            create_product(self.cache, self, new_id, new_product)
        return result


def make_store() -> tuple[TTLCache, ProductDatabase, FakeClock]:
    clock = FakeClock()
    return TTLCache(clock), ProductDatabase({"sku-1": MUG}), clock


class ReproduceProblem(unittest.TestCase):
    def test_every_lookup_of_a_missing_id_hits_the_database(self):
        cache, database, _ = make_store()
        for _ in range(3):
            naive_get_product(cache, database, "sku-404")
        self.assertEqual(database.query_count, 3)

    def test_scraper_sends_every_request_to_the_database(self):
        cache, database, _ = make_store()
        queries = simulate_scraper(naive_get_product, cache, database, MISSING_IDS,
                                   SCRAPER_ROUNDS, SECONDS_BETWEEN_ROUNDS)
        self.assertEqual(queries, 10_000)


class FixWithNegativeCaching(unittest.TestCase):
    def test_repeat_lookup_of_missing_id_is_served_from_cache(self):
        cache, database, _ = make_store()
        self.assertIsNone(get_product(cache, database, "sku-404"))
        self.assertIsNone(get_product(cache, database, "sku-404"))
        self.assertEqual(database.query_count, 1)

    def test_scraper_load_on_database_drops_tenfold(self):
        cache, database, _ = make_store()
        queries = simulate_scraper(get_product, cache, database, MISSING_IDS,
                                   SCRAPER_ROUNDS, SECONDS_BETWEEN_ROUNDS)
        self.assertEqual(queries, 1_000)

    def test_not_found_marker_expires_after_short_ttl(self):
        cache, database, clock = make_store()
        get_product(cache, database, "sku-404")
        clock.advance(NOT_FOUND_TTL_SECONDS)
        get_product(cache, database, "sku-404")
        self.assertEqual(database.query_count, 2)

    def test_created_product_is_visible_immediately(self):
        cache, database, _ = make_store()
        get_product(cache, database, "sku-new")
        create_product(cache, database, "sku-new", MUG)
        self.assertEqual(get_product(cache, database, "sku-new"), MUG)

    def test_product_inserted_without_invalidation_appears_after_ttl(self):
        cache, database, clock = make_store()
        get_product(cache, database, "sku-new")
        database.insert_product("sku-new", MUG)
        self.assertIsNone(get_product(cache, database, "sku-new"))
        clock.advance(NOT_FOUND_TTL_SECONDS)
        self.assertEqual(get_product(cache, database, "sku-new"), MUG)

    def test_read_overlapping_a_create_does_not_recache_not_found(self):
        clock = FakeClock()
        cache = TTLCache(clock)
        database = DatabaseWithConcurrentCreate(cache, "sku-new", MUG)
        self.assertIsNone(get_product(cache, database, "sku-new"))
        self.assertEqual(get_product(cache, database, "sku-new"), MUG)

    def test_read_without_overlapping_write_still_caches_not_found(self):
        cache, database, _ = make_store()
        get_product(cache, database, "sku-404")
        self.assertIs(cache.get("sku-404"), NOT_FOUND)

    def test_existing_products_are_still_cached_normally(self):
        cache, database, _ = make_store()
        self.assertEqual(get_product(cache, database, "sku-1"), MUG)
        self.assertEqual(get_product(cache, database, "sku-1"), MUG)
        self.assertEqual(database.query_count, 1)

    def test_marker_is_never_returned_to_callers(self):
        cache, database, _ = make_store()
        get_product(cache, database, "sku-404")
        self.assertIs(cache.get("sku-404"), NOT_FOUND)
        self.assertIsNone(get_product(cache, database, "sku-404"))


if __name__ == "__main__":
    unittest.main()
