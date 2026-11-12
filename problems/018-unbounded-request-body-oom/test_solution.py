import unittest

from solution import (HTTP_BAD_REQUEST, HTTP_OK, HTTP_PAYLOAD_TOO_LARGE, MAX_BODY_BYTES,
                      READ_CHUNK_BYTES, BodyRejected, FakeClientStream, Response, handle_upload,
                      naive_read_body, read_body)

MEBIBYTE = 1024 * 1024
HUGE_UPLOAD_BYTES = 64 * MEBIBYTE


def length_header(size: int) -> dict[str, str]:
    return {"Content-Length": str(size)}


class ReproduceProblem(unittest.TestCase):
    def test_naive_reader_buffers_the_entire_huge_body(self):
        stream = FakeClientStream(HUGE_UPLOAD_BYTES)
        body = naive_read_body(stream, length_header(HUGE_UPLOAD_BYTES))
        self.assertEqual(len(body), HUGE_UPLOAD_BYTES)
        self.assertEqual(stream.bytes_read, HUGE_UPLOAD_BYTES)


class FixWithBoundedReader(unittest.TestCase):
    def test_declared_oversized_body_is_rejected_without_reading_it(self):
        stream = FakeClientStream(HUGE_UPLOAD_BYTES)
        response = handle_upload(stream, length_header(HUGE_UPLOAD_BYTES))
        self.assertEqual(response.status, HTTP_PAYLOAD_TOO_LARGE)
        self.assertEqual(stream.bytes_read, 0)

    def test_chunked_oversized_body_stops_one_chunk_past_the_limit(self):
        stream = FakeClientStream(HUGE_UPLOAD_BYTES)
        response = handle_upload(stream, {})
        self.assertEqual(response.status, HTTP_PAYLOAD_TOO_LARGE)
        self.assertLessEqual(stream.bytes_read, MAX_BODY_BYTES + READ_CHUNK_BYTES)

    def test_normal_body_is_read_completely(self):
        response = handle_upload(FakeClientStream(2_000), length_header(2_000))
        self.assertEqual(response, handle_upload(FakeClientStream(2_000), {}))
        self.assertEqual(response.status, HTTP_OK)
        self.assertEqual(response.body, "received 2000 bytes")

    def test_body_exactly_at_the_limit_is_accepted(self):
        body = read_body(FakeClientStream(MAX_BODY_BYTES), length_header(MAX_BODY_BYTES))
        self.assertEqual(len(body), MAX_BODY_BYTES)

    def test_chunked_body_exactly_at_the_limit_is_accepted(self):
        self.assertEqual(len(read_body(FakeClientStream(MAX_BODY_BYTES), {})), MAX_BODY_BYTES)

    def test_one_byte_over_the_limit_is_rejected(self):
        with self.assertRaises(BodyRejected) as caught:
            read_body(FakeClientStream(MAX_BODY_BYTES + 1), {})
        self.assertEqual(caught.exception.status, HTTP_PAYLOAD_TOO_LARGE)

    def test_only_declared_bytes_are_read_when_client_sends_more(self):
        stream = FakeClientStream(5_000)
        self.assertEqual(len(read_body(stream, length_header(100))), 100)
        self.assertEqual(stream.bytes_read, 100)

    def test_body_shorter_than_declared_length_is_a_bad_request(self):
        response = handle_upload(FakeClientStream(10), length_header(100))
        self.assertEqual(response.status, HTTP_BAD_REQUEST)

    def test_malformed_content_length_values_are_rejected(self):
        for bad_value in ["-1", "abc", "+5", " 5", "1e3", "5_0", ""]:
            with self.subTest(value=bad_value):
                response = handle_upload(FakeClientStream(5), {"Content-Length": bad_value})
                self.assertEqual(response.status, HTTP_BAD_REQUEST)

    def test_empty_body_with_zero_length_is_accepted(self):
        self.assertEqual(read_body(FakeClientStream(0), length_header(0)), b"")


class FixHeaderNameCase(unittest.TestCase):
    def test_lowercase_oversized_length_is_rejected_without_reading(self):
        stream = FakeClientStream(HUGE_UPLOAD_BYTES)
        response = handle_upload(stream, {"content-length": str(HUGE_UPLOAD_BYTES)})
        self.assertEqual(response.status, HTTP_PAYLOAD_TOO_LARGE)
        self.assertEqual(stream.bytes_read, 0)

    def test_lowercase_length_reads_only_the_declared_bytes(self):
        stream = FakeClientStream(1_000)
        response = handle_upload(stream, {"content-length": "10"})
        self.assertEqual(response, Response(HTTP_OK, "received 10 bytes"))
        self.assertEqual(stream.remaining, 990)

    def test_conflicting_lengths_in_different_case_are_rejected(self):
        headers = {"Content-Length": "10", "content-length": "20"}
        response = handle_upload(FakeClientStream(100), headers)
        self.assertEqual(response.status, HTTP_BAD_REQUEST)


if __name__ == "__main__":
    unittest.main()
