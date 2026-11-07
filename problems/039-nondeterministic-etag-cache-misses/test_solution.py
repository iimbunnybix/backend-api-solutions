import os
import subprocess
import sys
import unittest
from pathlib import Path

from solution import (STATUS_NOT_MODIFIED, STATUS_OK, canonical_serialize,
                      if_none_match_hits, naive_respond, naive_serialize, respond,
                      strong_etag)

HERE = Path(__file__).resolve().parent
# Twelve API servers, each a separate Python process with its own hash seed.
SERVER_HASH_SEEDS = range(1, 13)

ETAG_SCRIPT = """
import sys
from solution import naive_serialize, canonical_serialize, strong_etag
product = {"sku": "LAMP-1", "price_cents": 2599,
           "tags": {"lighting", "desk", "led", "sale", "office", "dimmable"}}
serialize = naive_serialize if sys.argv[1] == "naive" else canonical_serialize
print(strong_etag(serialize(product)))
"""


def etag_on_server(hash_seed: int, mode: str) -> str:
    """ETag one server process computes for the same product."""
    env = dict(os.environ, PYTHONHASHSEED=str(hash_seed))
    result = subprocess.run([sys.executable, "-c", ETAG_SCRIPT, mode], cwd=HERE,
                            env=env, capture_output=True, text=True, check=True)
    return result.stdout.strip()


def product_with_attributes_in_order(order: list[str]) -> dict:
    """Same attributes, inserted in the order their sources answered."""
    values = {"color": "black", "watts": 9, "material": "steel"}
    attributes = {}
    for name in order:
        attributes[name] = values[name]
    return {"sku": "LAMP-1", "attributes": attributes}


class ReproduceProblem(unittest.TestCase):
    def test_servers_disagree_on_the_etag_of_the_same_product(self):
        etags = {etag_on_server(seed, "naive") for seed in SERVER_HASH_SEEDS}
        self.assertGreater(len(etags), 1)

    def test_attribute_insertion_order_changes_the_etag(self):
        first = product_with_attributes_in_order(["color", "watts", "material"])
        second = product_with_attributes_in_order(["material", "color", "watts"])
        self.assertEqual(first, second)
        self.assertNotEqual(strong_etag(naive_serialize(first)),
                            strong_etag(naive_serialize(second)))

    def test_client_revalidating_on_another_server_gets_full_body(self):
        first = product_with_attributes_in_order(["color", "watts", "material"])
        second = product_with_attributes_in_order(["watts", "material", "color"])
        _, headers, _ = naive_respond(first, None)
        status, _, payload = naive_respond(second, headers["ETag"])
        self.assertEqual(status, STATUS_OK)
        self.assertTrue(payload)


class FixCanonicalEtag(unittest.TestCase):
    def test_every_server_computes_the_same_etag(self):
        etags = {etag_on_server(seed, "canonical") for seed in SERVER_HASH_SEEDS}
        self.assertEqual(len(etags), 1)

    def test_attribute_insertion_order_does_not_change_the_etag(self):
        first = product_with_attributes_in_order(["color", "watts", "material"])
        second = product_with_attributes_in_order(["material", "color", "watts"])
        self.assertEqual(canonical_serialize(first), canonical_serialize(second))

    def test_unchanged_product_revalidates_with_304_and_empty_body(self):
        first = product_with_attributes_in_order(["color", "watts", "material"])
        second = product_with_attributes_in_order(["watts", "material", "color"])
        _, headers, _ = respond(first, None)
        status, _, payload = respond(second, headers["ETag"])
        self.assertEqual(status, STATUS_NOT_MODIFIED)
        self.assertEqual(payload, b"")

    def test_changed_product_gets_a_new_etag_and_full_body(self):
        product = product_with_attributes_in_order(["color", "watts", "material"])
        _, headers, _ = respond(product, None)
        product["attributes"]["watts"] = 12
        status, new_headers, payload = respond(product, headers["ETag"])
        self.assertEqual(status, STATUS_OK)
        self.assertNotEqual(new_headers["ETag"], headers["ETag"])
        self.assertIn(b'"watts":12', payload)

    def test_etag_matches_the_bytes_actually_sent(self):
        _, headers, payload = respond({"tags": {"b", "a"}}, None)
        self.assertEqual(headers["ETag"], strong_etag(payload))
        self.assertEqual(payload, b'{"tags":["a","b"]}')


class CanonicalEdgeCases(unittest.TestCase):
    def test_list_order_is_preserved_because_it_is_content(self):
        self.assertEqual(canonical_serialize({"steps": [3, 1, 2]}), b'{"steps":[3,1,2]}')

    def test_nested_sets_inside_lists_are_sorted(self):
        body = {"variants": [{"sizes": {"M", "S", "L"}}]}
        self.assertEqual(canonical_serialize(body), b'{"variants":[{"sizes":["L","M","S"]}]}')

    def test_set_with_mixed_types_does_not_crash(self):
        self.assertEqual(canonical_serialize({"ids": {2, "1", None}}),
                         b'{"ids":["1",2,null]}')

    def test_non_ascii_text_is_kept_as_utf8(self):
        self.assertEqual(canonical_serialize({"name": "Café"}), '{"name":"Café"}'.encode())

    def test_nan_is_rejected_rather_than_sent_as_invalid_json(self):
        with self.assertRaises(ValueError):
            canonical_serialize({"price": float("nan")})


class IfNoneMatchParsing(unittest.TestCase):
    def test_missing_header_never_matches(self):
        self.assertFalse(if_none_match_hits(None, '"abc"'))

    def test_star_matches_any_current_representation(self):
        self.assertTrue(if_none_match_hits("*", '"abc"'))

    def test_weak_tag_matches_for_if_none_match(self):
        self.assertTrue(if_none_match_hits('W/"abc"', '"abc"'))

    def test_list_of_tags_matches_if_any_is_current(self):
        self.assertTrue(if_none_match_hits('"old", "abc"', '"abc"'))

    def test_unquoted_value_does_not_match(self):
        self.assertFalse(if_none_match_hits("abc", '"abc"'))


if __name__ == "__main__":
    unittest.main()
