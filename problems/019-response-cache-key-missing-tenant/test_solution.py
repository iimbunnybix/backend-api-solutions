import unittest

from solution import (
    NaiveResponseCache,
    Request,
    VaryAwareResponseCache,
    VarySpec,
    build_cache_key,
)

PRICES_PATH = "/catalog/prices"
PRICES_VARY = VarySpec(uses_tenant=True, headers=("Accept-Language",), query_params=("currency",))


class CountingPriceHandler:
    """Fake handler: renders a body from the inputs and counts how often it ran."""

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, request: Request) -> str:
        self.calls += 1
        language = request.headers.get("Accept-Language", "default")
        currency = request.query.get("currency", "default")
        return f"prices for {request.tenant_id} in {language}/{currency}"


def price_request(tenant: str, language: str | None = "en", currency: str | None = "EUR") -> Request:
    headers = {} if language is None else {"Accept-Language": language}
    query = {} if currency is None else {"currency": currency}
    return Request(path=PRICES_PATH, tenant_id=tenant, headers=headers, query=query)


def vary_cache() -> VaryAwareResponseCache:
    cache = VaryAwareResponseCache()
    cache.register_route(PRICES_PATH, PRICES_VARY)
    return cache


class ReproduceProblem(unittest.TestCase):
    def test_second_tenant_is_served_first_tenants_prices(self):
        cache = NaiveResponseCache()
        handler = CountingPriceHandler()
        cache.get_or_compute(price_request("shop-a"), handler)
        body = cache.get_or_compute(price_request("shop-b"), handler)
        self.assertEqual(body, "prices for shop-a in en/EUR")

    def test_currency_change_is_ignored(self):
        cache = NaiveResponseCache()
        handler = CountingPriceHandler()
        cache.get_or_compute(price_request("shop-a", currency="EUR"), handler)
        body = cache.get_or_compute(price_request("shop-a", currency="USD"), handler)
        self.assertIn("EUR", body)


class FixVaryAwareCache(unittest.TestCase):
    def test_each_tenant_gets_its_own_prices(self):
        cache = vary_cache()
        handler = CountingPriceHandler()
        cache.get_or_compute(price_request("shop-a"), handler)
        body = cache.get_or_compute(price_request("shop-b"), handler)
        self.assertEqual(body, "prices for shop-b in en/EUR")

    def test_language_and_currency_get_separate_entries(self):
        cache = vary_cache()
        handler = CountingPriceHandler()
        cache.get_or_compute(price_request("shop-a", language="en"), handler)
        cache.get_or_compute(price_request("shop-a", language="de"), handler)
        cache.get_or_compute(price_request("shop-a", currency="USD"), handler)
        self.assertEqual(handler.calls, 3)

    def test_identical_requests_hit_the_cache(self):
        cache = vary_cache()
        handler = CountingPriceHandler()
        for _ in range(5):
            cache.get_or_compute(price_request("shop-a"), handler)
        self.assertEqual(handler.calls, 1)

    def test_header_sent_in_two_casings_does_not_poison_the_cache(self):
        cache = vary_cache()
        handler = CountingPriceHandler()
        ambiguous = Request(
            path=PRICES_PATH,
            tenant_id="shop-a",
            headers={"Accept-Language": "en", "accept-language": "de"},
            query={"currency": "EUR"},
        )
        cache.get_or_compute(ambiguous, handler)
        body = cache.get_or_compute(price_request("shop-a", language="de"), handler)
        self.assertEqual(body, "prices for shop-a in de/EUR")

    def test_route_without_vary_spec_is_never_cached(self):
        cache = VaryAwareResponseCache()
        handler = CountingPriceHandler()
        cache.get_or_compute(price_request("shop-a"), handler)
        cache.get_or_compute(price_request("shop-a"), handler)
        self.assertEqual(handler.calls, 2)


class FixCacheKey(unittest.TestCase):
    def test_header_name_case_does_not_matter(self):
        upper = Request(PRICES_PATH, "shop-a", headers={"ACCEPT-LANGUAGE": "en"})
        lower = Request(PRICES_PATH, "shop-a", headers={"accept-language": "en"})
        self.assertEqual(build_cache_key(upper, PRICES_VARY), build_cache_key(lower, PRICES_VARY))

    def test_missing_header_differs_from_empty_header(self):
        missing = price_request("shop-a", language=None)
        empty = price_request("shop-a", language="")
        self.assertNotEqual(build_cache_key(missing, PRICES_VARY), build_cache_key(empty, PRICES_VARY))

    def test_values_cannot_be_shifted_between_fields_to_collide(self):
        vary = VarySpec(uses_tenant=True, query_params=("a", "b"))
        first = Request(PRICES_PATH, "t", query={"a": "x\x1fq\x1fb", "b": "y"})
        second = Request(PRICES_PATH, "t", query={"a": "x", "b": "y"})
        self.assertNotEqual(build_cache_key(first, vary), build_cache_key(second, vary))

    def test_tenant_ids_that_look_like_paths_do_not_collide(self):
        first = Request("/a", "b/c")
        second = Request("/a/b", "c")
        vary = VarySpec(uses_tenant=True)
        self.assertNotEqual(build_cache_key(first, vary), build_cache_key(second, vary))

    def test_unlisted_header_does_not_split_the_cache(self):
        with_trace = Request(PRICES_PATH, "shop-a", headers={"X-Request-Id": "1"})
        other_trace = Request(PRICES_PATH, "shop-a", headers={"X-Request-Id": "2"})
        self.assertEqual(build_cache_key(with_trace, PRICES_VARY), build_cache_key(other_trace, PRICES_VARY))


if __name__ == "__main__":
    unittest.main()
