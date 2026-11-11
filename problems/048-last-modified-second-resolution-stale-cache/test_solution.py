import unittest

from solution import (HTTP_NOT_MODIFIED, HTTP_OK, FakeClock, ProductStore, fixed_get,
                      http_date, naive_get, same_second_correction)

START = 1_762_862_400.2
TYPO_PRICE = 19_990
CORRECT_PRICE = 1_999


def store_with_price(price_cents: int, now: float = START) -> ProductStore:
    """A store holding one product saved at ``now``."""
    store = ProductStore(FakeClock(now))
    store.set_price("sku-1", price_cents)
    return store


class ReproduceProblem(unittest.TestCase):
    def test_correction_in_the_same_second_is_never_seen_by_the_app(self):
        self.assertEqual(same_second_correction(naive_get), TYPO_PRICE)

    def test_naive_handler_answers_304_although_the_price_changed(self):
        store = store_with_price(TYPO_PRICE)
        first = naive_get(store, "sku-1", {})
        store.clock.now += 0.5
        store.set_price("sku-1", CORRECT_PRICE)
        since = {"If-Modified-Since": first.headers["Last-Modified"]}
        self.assertEqual(naive_get(store, "sku-1", since).status, HTTP_NOT_MODIFIED)


class FixWithVersionEtag(unittest.TestCase):
    def test_correction_in_the_same_second_reaches_the_app(self):
        self.assertEqual(same_second_correction(fixed_get), CORRECT_PRICE)

    def test_unchanged_product_is_revalidated_with_304(self):
        store = store_with_price(CORRECT_PRICE)
        etag = fixed_get(store, "sku-1", {}).headers["ETag"]
        response = fixed_get(store, "sku-1", {"If-None-Match": etag})
        self.assertEqual(response.status, HTTP_NOT_MODIFIED)

    def test_etag_mismatch_wins_over_a_matching_if_modified_since(self):
        store = store_with_price(TYPO_PRICE)
        old_etag = fixed_get(store, "sku-1", {}).headers["ETag"]
        store.clock.now += 0.5
        store.set_price("sku-1", CORRECT_PRICE)
        headers = {"If-None-Match": old_etag, "If-Modified-Since": http_date(START + 5)}
        self.assertEqual(fixed_get(store, "sku-1", headers).status, HTTP_OK)

    def test_matching_etag_in_a_list_gives_304(self):
        store = store_with_price(CORRECT_PRICE)
        headers = {"If-None-Match": '"v9", "v1"'}
        self.assertEqual(fixed_get(store, "sku-1", headers).status, HTTP_NOT_MODIFIED)


class FixLastModifiedTiming(unittest.TestCase):
    def test_last_modified_is_withheld_during_the_modification_second(self):
        store = store_with_price(CORRECT_PRICE)
        store.clock.now += 0.5
        self.assertNotIn("Last-Modified", fixed_get(store, "sku-1", {}).headers)

    def test_last_modified_is_sent_once_the_second_is_over(self):
        store = store_with_price(CORRECT_PRICE)
        store.clock.now += 1
        self.assertEqual(fixed_get(store, "sku-1", {}).headers["Last-Modified"], http_date(START))

    def test_if_modified_since_alone_still_gives_304_for_old_changes(self):
        store = store_with_price(CORRECT_PRICE)
        store.clock.now += 10
        since = {"If-Modified-Since": http_date(START)}
        self.assertEqual(fixed_get(store, "sku-1", since).status, HTTP_NOT_MODIFIED)

    def test_if_modified_since_for_the_current_second_is_not_trusted(self):
        store = store_with_price(CORRECT_PRICE)
        store.clock.now += 0.5
        since = {"If-Modified-Since": http_date(START)}
        self.assertEqual(fixed_get(store, "sku-1", since).status, HTTP_OK)

    def test_change_after_last_modified_was_sent_is_detected(self):
        store = store_with_price(TYPO_PRICE)
        store.clock.now += 1
        since = {"If-Modified-Since": fixed_get(store, "sku-1", {}).headers["Last-Modified"]}
        store.set_price("sku-1", CORRECT_PRICE)
        self.assertEqual(fixed_get(store, "sku-1", since).price_cents, CORRECT_PRICE)


if __name__ == "__main__":
    unittest.main()
