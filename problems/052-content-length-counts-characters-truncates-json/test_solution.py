import json
import unittest

from solution import build_response, naive_build_response, read_one_response, read_responses

ASCII_BODY = json.dumps({"name": "Zoe", "price": "499"})
NON_ASCII_BODY = json.dumps({"name": "Zoë", "price": "₹499"}, ensure_ascii=False)


class ReproduceProblem(unittest.TestCase):
    def test_ascii_body_happens_to_work(self):
        [response] = read_responses(naive_build_response(ASCII_BODY))
        self.assertEqual(json.loads(response.body), {"name": "Zoe", "price": "499"})

    def test_non_ascii_body_is_cut_short(self):
        response, leftover = read_one_response(naive_build_response(NON_ASCII_BODY))
        self.assertEqual(leftover, b'9"}')
        with self.assertRaises(ValueError):
            json.loads(response.body)

    def test_leftover_bytes_corrupt_the_next_response_on_keep_alive(self):
        stream = naive_build_response(NON_ASCII_BODY) + build_response("{}")
        with self.assertRaises(ValueError):
            read_responses(stream)


class FixByteLength(unittest.TestCase):
    def test_content_length_counts_utf8_bytes(self):
        [response] = read_responses(build_response(NON_ASCII_BODY))
        self.assertEqual(int(response.headers["content-length"]), len(NON_ASCII_BODY.encode("utf-8")))

    def test_non_ascii_body_parses_completely(self):
        [response] = read_responses(build_response(NON_ASCII_BODY))
        self.assertEqual(json.loads(response.body), {"name": "Zoë", "price": "₹499"})

    def test_keep_alive_responses_stay_in_sync(self):
        stream = build_response(NON_ASCII_BODY) + build_response('{"ok": true}')
        first, second = read_responses(stream)
        self.assertEqual(json.loads(second.body), {"ok": True})

    def test_charset_is_declared(self):
        [response] = read_responses(build_response(ASCII_BODY))
        self.assertIn("charset=utf-8", response.headers["content-type"])

    def test_emoji_and_empty_body(self):
        [emoji, empty] = read_responses(build_response('"🚀"') + build_response(""))
        self.assertEqual(json.loads(emoji.body), "🚀")
        self.assertEqual(empty.body, b"")


if __name__ == "__main__":
    unittest.main()
