import gc
import unittest
import weakref

from solution import (CatalogClient, FakeClock, NaivePricingService, PricingService, Request,
                      SharedPriceCache)

PRICES = {"apple": 120, "bread": 250, "milk": 99, "eggs": 310, "rice": 540}
CART = ["apple", "bread", "milk"]
REQUESTS = 1000
BODY_BYTES = 10_000


def make_request(number):
    return Request(f"req-{number}", b"x" * BODY_BYTES)


def run_naive_requests(catalog, count=REQUESTS):
    """Serve ``count`` checkouts; return weak references to the finished services."""
    finished = []
    for number in range(count):
        service = NaivePricingService(make_request(number), catalog)
        service.cart_total_cents(CART)
        finished.append(weakref.ref(service))
    gc.collect()
    return finished


def count_alive(references):
    alive = 0
    for reference in references:
        if reference() is not None:
            alive += 1
    return alive


class ReproduceProblem(unittest.TestCase):
    def setUp(self):
        NaivePricingService.price_for.cache_clear()

    def tearDown(self):
        NaivePricingService.price_for.cache_clear()

    def test_cache_never_hits_across_requests(self):
        catalog = CatalogClient(PRICES)
        run_naive_requests(catalog)
        self.assertEqual(catalog.calls, REQUESTS * len(CART))

    def test_every_finished_request_is_kept_alive_by_the_cache(self):
        finished = run_naive_requests(CatalogClient(PRICES))
        self.assertEqual(count_alive(finished), REQUESTS)

    def test_cache_grows_by_one_entry_per_request_and_sku(self):
        run_naive_requests(CatalogClient(PRICES))
        self.assertEqual(NaivePricingService.price_for.cache_info().currsize, REQUESTS * len(CART))

    def test_retained_request_bodies_add_up_to_ten_megabytes(self):
        finished = run_naive_requests(CatalogClient(PRICES))
        retained = 0
        for reference in finished:
            retained += len(reference().request.body)
        self.assertEqual(retained, REQUESTS * BODY_BYTES)


class FixSharedPriceCache(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.catalog = CatalogClient(PRICES)
        self.cache = SharedPriceCache(self.catalog, self.clock)

    def serve(self, number):
        return PricingService(make_request(number), self.cache).cart_total_cents(CART)

    def test_cart_total_is_correct(self):
        self.assertEqual(self.serve(0), 120 + 250 + 99)

    def test_catalog_is_called_once_per_sku_across_all_requests(self):
        for number in range(REQUESTS):
            self.serve(number)
        self.assertEqual(self.catalog.calls, len(CART))

    def test_finished_requests_are_garbage_collected(self):
        finished = []
        for number in range(REQUESTS):
            service = PricingService(make_request(number), self.cache)
            service.cart_total_cents(CART)
            finished.append(weakref.ref(service))
        del service  # drop the loop variable's reference to the last request
        gc.collect()
        self.assertEqual(count_alive(finished), 0)

    def test_price_is_refetched_after_the_ttl(self):
        self.serve(0)
        self.clock.advance(self.cache.ttl_seconds)
        self.catalog.prices["apple"] = 150
        self.assertEqual(self.serve(1), 150 + 250 + 99)
        self.assertEqual(self.catalog.calls, 2 * len(CART))

    def test_price_is_reused_just_before_the_ttl(self):
        self.serve(0)
        self.clock.advance(self.cache.ttl_seconds - 1)
        self.serve(1)
        self.assertEqual(self.catalog.calls, len(CART))

    def test_cache_never_exceeds_its_size_limit(self):
        small_cache = SharedPriceCache(self.catalog, self.clock, max_entries=2)
        for sku in PRICES:
            small_cache.price_for(sku)
        self.assertEqual(len(small_cache), 2)

    def test_least_recently_used_sku_is_evicted_first(self):
        small_cache = SharedPriceCache(self.catalog, self.clock, max_entries=2)
        small_cache.price_for("apple")
        small_cache.price_for("bread")
        small_cache.price_for("apple")      # apple is now the most recent
        small_cache.price_for("milk")       # evicts bread
        calls_before = self.catalog.calls
        small_cache.price_for("apple")
        self.assertEqual(self.catalog.calls, calls_before)
        small_cache.price_for("bread")
        self.assertEqual(self.catalog.calls, calls_before + 1)


if __name__ == "__main__":
    unittest.main()
