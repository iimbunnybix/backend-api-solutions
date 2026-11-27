import unittest

from solution import (CONTENT_ENCODING, FIRMWARE_MANIFEST, GZIP, IDENTITY, OriginalUrlOnlyCache,
                      VaryAwareCache, fixed_origin, modern_client_parse, naive_origin,
                      normalise_accept_encoding, plain_client_parse)

MANIFEST_PATH = "/v1/firmware/thermo-2/manifest"
BROWSER_HEADERS = {"Accept-Encoding": "gzip, deflate, br"}
PLAIN_CLIENT_HEADERS: dict[str, str] = {}


def fixed_cache() -> VaryAwareCache:
    """The fixed setup: an origin that sends Vary and a cache that honours it."""
    return VaryAwareCache(fixed_origin())


class ReproduceProblem(unittest.TestCase):
    def test_plain_client_gets_gzip_bytes_cached_for_a_browser(self):
        cache = OriginalUrlOnlyCache(naive_origin())
        cache.get(MANIFEST_PATH, BROWSER_HEADERS)
        response = cache.get(MANIFEST_PATH, PLAIN_CLIENT_HEADERS)
        self.assertEqual(response.headers[CONTENT_ENCODING], GZIP)
        with self.assertRaises(ValueError):
            plain_client_parse(response)

    def test_vary_aware_cache_alone_cannot_help_without_vary_from_origin(self):
        cache = VaryAwareCache(naive_origin())
        cache.get(MANIFEST_PATH, BROWSER_HEADERS)
        with self.assertRaises(ValueError):
            plain_client_parse(cache.get(MANIFEST_PATH, PLAIN_CLIENT_HEADERS))

    def test_plain_client_works_when_it_happens_to_fill_the_cache_first(self):
        cache = OriginalUrlOnlyCache(naive_origin())
        response = cache.get(MANIFEST_PATH, PLAIN_CLIENT_HEADERS)
        self.assertEqual(plain_client_parse(response), FIRMWARE_MANIFEST)


class FixNormaliseAcceptEncoding(unittest.TestCase):
    def test_common_browser_spellings_all_mean_gzip(self):
        for value in ("gzip", "gzip, deflate, br", "br;q=1.0, gzip;q=0.8", "GZIP", "*"):
            with self.subTest(value=value):
                self.assertEqual(normalise_accept_encoding(value), GZIP)

    def test_missing_or_unsupported_encodings_mean_identity(self):
        for value in (None, "", "br", "deflate", "identity"):
            with self.subTest(value=value):
                self.assertEqual(normalise_accept_encoding(value), IDENTITY)

    def test_gzip_with_zero_quality_is_a_refusal(self):
        self.assertEqual(normalise_accept_encoding("gzip;q=0, br"), IDENTITY)


class FixVaryAwareCache(unittest.TestCase):
    def test_plain_client_gets_uncompressed_json_after_a_browser_filled_the_cache(self):
        cache = fixed_cache()
        cache.get(MANIFEST_PATH, BROWSER_HEADERS)
        response = cache.get(MANIFEST_PATH, PLAIN_CLIENT_HEADERS)
        self.assertNotIn(CONTENT_ENCODING, response.headers)
        self.assertEqual(plain_client_parse(response), FIRMWARE_MANIFEST)

    def test_browser_still_gets_gzip_after_a_plain_client_filled_the_cache(self):
        cache = fixed_cache()
        cache.get(MANIFEST_PATH, PLAIN_CLIENT_HEADERS)
        response = cache.get(MANIFEST_PATH, BROWSER_HEADERS)
        self.assertEqual(response.headers[CONTENT_ENCODING], GZIP)
        self.assertEqual(modern_client_parse(response), FIRMWARE_MANIFEST)

    def test_many_accept_encoding_spellings_share_two_entries(self):
        cache = fixed_cache()
        spellings = ["gzip", "gzip, deflate", "gzip, deflate, br", "br;q=1.0, gzip;q=0.8",
                     None, "br"]
        for spelling in spellings:
            headers = {} if spelling is None else {"Accept-Encoding": spelling}
            cache.get(MANIFEST_PATH, headers)
        self.assertEqual(len(cache.entries), 2)
        self.assertEqual(cache.origin.requests_served, 2)

    def test_header_name_case_does_not_matter(self):
        cache = fixed_cache()
        cache.get(MANIFEST_PATH, {"accept-encoding": "gzip"})
        cache.get(MANIFEST_PATH, {"ACCEPT-ENCODING": "gzip"})
        self.assertEqual(cache.origin.requests_served, 1)

    def test_repeat_requests_are_served_from_cache(self):
        cache = fixed_cache()
        for _ in range(100):
            cache.get(MANIFEST_PATH, BROWSER_HEADERS)
            cache.get(MANIFEST_PATH, PLAIN_CLIENT_HEADERS)
        self.assertEqual(cache.origin.requests_served, 2)

    def test_different_urls_get_separate_entries(self):
        cache = fixed_cache()
        cache.get(MANIFEST_PATH, BROWSER_HEADERS)
        cache.get("/v1/firmware/thermo-3/manifest", BROWSER_HEADERS)
        self.assertEqual(cache.origin.requests_served, 2)

    def test_gzip_body_is_actually_smaller(self):
        cache = fixed_cache()
        compressed = cache.get(MANIFEST_PATH, BROWSER_HEADERS).body
        plain = cache.get(MANIFEST_PATH, PLAIN_CLIENT_HEADERS).body
        self.assertLess(len(compressed), len(plain))


if __name__ == "__main__":
    unittest.main()
