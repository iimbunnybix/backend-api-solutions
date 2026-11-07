import io
import unittest

from solution import make_replayable, naive_post_with_retry, post_with_retry

DOCUMENT = b"%PDF-1.7 quarterly report " * 1000
UPLOAD_URL = "https://docs.example.test/upload"


class FlakyUploadServer:
    """Reads the whole body, then resets the connection on the first N attempts.

    This is the real failure shape: the body was fully sent, but the response
    never arrived (a proxy idle reset, a load balancer draining a connection).
    """

    def __init__(self, resets_before_success: int) -> None:
        self.resets_left = resets_before_success
        self.received_bodies: list[bytes] = []

    def send(self, url: str, chunks) -> int:
        body = b"".join(chunks)
        self.received_bodies.append(body)
        if self.resets_left > 0:
            self.resets_left -= 1
            raise ConnectionResetError("connection reset by peer")
        return 201

    @property
    def stored_document(self) -> bytes:
        """The body of the attempt the server answered with 201."""
        return self.received_bodies[-1]


def document_stream(data: bytes = DOCUMENT, chunk: int = 4096):
    """A one-shot generator, like streaming from object storage."""
    for start in range(0, len(data), chunk):
        yield data[start:start + chunk]


class ReproduceProblem(unittest.TestCase):
    def test_retry_of_a_generator_body_stores_an_empty_document(self):
        server = FlakyUploadServer(resets_before_success=1)
        status = naive_post_with_retry(server, UPLOAD_URL, document_stream())
        self.assertEqual(status, 201)
        self.assertEqual(server.stored_document, b"")

    def test_retry_of_a_file_body_stores_an_empty_document(self):
        server = FlakyUploadServer(resets_before_success=1)
        naive_post_with_retry(server, UPLOAD_URL, io.BytesIO(DOCUMENT))
        self.assertEqual(server.stored_document, b"")

    def test_first_attempt_did_send_the_full_document(self):
        server = FlakyUploadServer(resets_before_success=1)
        naive_post_with_retry(server, UPLOAD_URL, document_stream())
        self.assertEqual(server.received_bodies[0], DOCUMENT)


class FixReplayableBody(unittest.TestCase):
    def test_generator_body_is_resent_in_full(self):
        server = FlakyUploadServer(resets_before_success=2)
        status = post_with_retry(server, UPLOAD_URL, document_stream())
        self.assertEqual(status, 201)
        self.assertEqual(server.received_bodies, [DOCUMENT] * 3)

    def test_file_body_is_rewound_for_each_attempt(self):
        server = FlakyUploadServer(resets_before_success=1)
        post_with_retry(server, UPLOAD_URL, io.BytesIO(DOCUMENT))
        self.assertEqual(server.stored_document, DOCUMENT)

    def test_file_is_rewound_to_its_starting_offset_not_to_zero(self):
        file = io.BytesIO(b"HEADER|" + DOCUMENT)
        file.seek(len(b"HEADER|"))
        server = FlakyUploadServer(resets_before_success=1)
        post_with_retry(server, UPLOAD_URL, file)
        self.assertEqual(server.stored_document, DOCUMENT)

    def test_bytes_body_is_resent_in_full(self):
        server = FlakyUploadServer(resets_before_success=1)
        post_with_retry(server, UPLOAD_URL, DOCUMENT)
        self.assertEqual(server.stored_document, DOCUMENT)

    def test_gives_up_after_max_attempts_with_the_last_error(self):
        server = FlakyUploadServer(resets_before_success=5)
        with self.assertRaises(ConnectionResetError):
            post_with_retry(server, UPLOAD_URL, DOCUMENT, max_attempts=3)
        self.assertEqual(len(server.received_bodies), 3)


class LargeStreams(unittest.TestCase):
    def test_stream_over_the_buffer_limit_is_sent_once_in_full(self):
        server = FlakyUploadServer(resets_before_success=0)
        post_with_retry(server, UPLOAD_URL, document_stream(), buffer_limit=10_000)
        self.assertEqual(server.received_bodies, [DOCUMENT])

    def test_stream_over_the_buffer_limit_is_not_retried(self):
        server = FlakyUploadServer(resets_before_success=1)
        with self.assertRaises(ConnectionResetError):
            post_with_retry(server, UPLOAD_URL, document_stream(), buffer_limit=10_000)
        self.assertEqual(len(server.received_bodies), 1)

    def test_one_shot_body_refuses_a_second_send(self):
        body = make_replayable(document_stream(), limit=10_000)
        self.assertFalse(body.can_replay)
        b"".join(body.chunks())
        with self.assertRaises(RuntimeError):
            body.chunks()

    def test_stream_exactly_at_the_limit_is_still_replayable(self):
        body = make_replayable(document_stream(), limit=len(DOCUMENT))
        self.assertTrue(body.can_replay)


class EdgeCases(unittest.TestCase):
    def test_empty_body_stays_empty_on_retry(self):
        server = FlakyUploadServer(resets_before_success=1)
        post_with_retry(server, UPLOAD_URL, iter([]))
        self.assertEqual(server.received_bodies, [b"", b""])

    def test_non_seekable_file_like_is_buffered(self):
        class Pipe(io.RawIOBase):
            def __init__(self, data: bytes) -> None:
                self._source = io.BytesIO(data)

            def readable(self) -> bool:
                return True

            def readinto(self, buffer) -> int:
                data = self._source.read(len(buffer))
                buffer[:len(data)] = data
                return len(data)

        server = FlakyUploadServer(resets_before_success=1)
        post_with_retry(server, UPLOAD_URL, Pipe(DOCUMENT))
        self.assertEqual(server.stored_document, DOCUMENT)


if __name__ == "__main__":
    unittest.main()
