import unittest

from solution import (STATUS_NOT_MODIFIED, STATUS_OK, cdn_weaken, is_not_modified,
                      naive_is_not_modified, parse_if_none_match, serve_product)

PRODUCT_BODY = b'{"sku": "lamp-7", "price": "49.00"}' * 100
CURRENT_ETAG = '"v42"'
OLD_ETAG = '"v41"'


def naive_serve(if_none_match):
    """Serve the product with the original exact-match check."""
    return serve_product(PRODUCT_BODY, CURRENT_ETAG, if_none_match, check=naive_is_not_modified)


def fixed_serve(if_none_match):
    """Serve the product with the RFC 9110 weak comparison."""
    return serve_product(PRODUCT_BODY, CURRENT_ETAG, if_none_match)


class ReproduceProblem(unittest.TestCase):
    def test_exact_strong_etag_gets_304(self):
        self.assertEqual(naive_serve(CURRENT_ETAG).status, STATUS_NOT_MODIFIED)

    def test_etag_weakened_by_cdn_never_gets_304(self):
        response = naive_serve(cdn_weaken(CURRENT_ETAG))
        self.assertEqual(response.status, STATUS_OK)
        self.assertEqual(response.body, PRODUCT_BODY)

    def test_list_of_etags_never_gets_304(self):
        self.assertEqual(naive_serve(f'{OLD_ETAG}, {CURRENT_ETAG}').status, STATUS_OK)


class FixWeakComparison(unittest.TestCase):
    def test_etag_weakened_by_cdn_gets_304_with_empty_body(self):
        response = fixed_serve(cdn_weaken(CURRENT_ETAG))
        self.assertEqual((response.status, response.body), (STATUS_NOT_MODIFIED, b""))

    def test_exact_strong_etag_still_gets_304(self):
        self.assertEqual(fixed_serve(CURRENT_ETAG).status, STATUS_NOT_MODIFIED)

    def test_old_etag_gets_full_body(self):
        self.assertEqual(fixed_serve(OLD_ETAG).status, STATUS_OK)

    def test_old_weak_etag_gets_full_body(self):
        self.assertEqual(fixed_serve('W/"v41"').status, STATUS_OK)

    def test_current_etag_anywhere_in_list_gets_304(self):
        self.assertEqual(fixed_serve('W/"v40" , W/"v42"').status, STATUS_NOT_MODIFIED)

    def test_star_matches_existing_resource(self):
        self.assertTrue(is_not_modified(" * ", CURRENT_ETAG))

    def test_missing_header_gets_full_body(self):
        self.assertEqual(fixed_serve(None).status, STATUS_OK)

    def test_unquoted_value_does_not_match(self):
        self.assertFalse(is_not_modified("v42", CURRENT_ETAG))

    def test_value_without_quotes_inside_weak_prefix_does_not_match(self):
        self.assertFalse(is_not_modified("W/v42", CURRENT_ETAG))

    def test_quoted_value_is_compared_case_sensitively(self):
        self.assertFalse(is_not_modified('"V42"', CURRENT_ETAG))


class ParseHelpers(unittest.TestCase):
    def test_parser_skips_malformed_items(self):
        tags = parse_if_none_match('"a", junk, W/"b"')
        self.assertEqual([(tag.opaque, tag.weak) for tag in tags], [('"a"', False), ('"b"', True)])

    def test_cdn_does_not_double_weaken(self):
        self.assertEqual(cdn_weaken('W/"v42"'), 'W/"v42"')


if __name__ == "__main__":
    unittest.main()
