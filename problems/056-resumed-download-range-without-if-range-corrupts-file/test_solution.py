import hashlib
import unittest

from solution import (
    STATUS_OK,
    STATUS_PARTIAL_CONTENT,
    STATUS_RANGE_NOT_SATISFIABLE,
    FileStore,
    handle_get,
    if_range_matches,
    naive_handle_get,
    parse_range,
    resume_download,
)

FILE_SIZE = 1000
# Where the customer's connection dropped during the first download.
BYTES_RECEIVED_BEFORE_DROP = 400


def make_release(version: int) -> bytes:
    """Deterministic fake installer bytes; each version differs everywhere."""
    pattern = f"installer-v{version}|".encode()
    return (pattern * FILE_SIZE)[:FILE_SIZE]


def checksum(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def start_download(store: FileStore) -> tuple[bytes, str]:
    """First request: full 200, but only part of the body arrives."""
    response = handle_get(store, {})
    return response.body[:BYTES_RECEIVED_BEFORE_DROP], response.headers["ETag"]


class ReproduceProblem(unittest.TestCase):
    def test_resume_after_new_release_splices_two_files(self):
        store = FileStore(make_release(1))
        partial, etag = start_download(store)
        store.replace(make_release(2))
        downloaded = resume_download(naive_handle_get, store, partial, etag)
        self.assertEqual(len(downloaded), FILE_SIZE)
        self.assertNotEqual(checksum(downloaded), checksum(make_release(1)))
        self.assertNotEqual(checksum(downloaded), checksum(make_release(2)))

    def test_naive_server_ignores_a_stale_if_range(self):
        store = FileStore(make_release(1))
        _partial, old_etag = start_download(store)
        store.replace(make_release(2))
        response = naive_handle_get(store, {"Range": "bytes=400-", "If-Range": old_etag})
        self.assertEqual(response.status, STATUS_PARTIAL_CONTENT)


class FixHonourIfRange(unittest.TestCase):
    def test_resume_without_a_new_release_gets_only_the_rest(self):
        store = FileStore(make_release(1))
        partial, etag = start_download(store)
        response = handle_get(store, {"Range": "bytes=400-", "If-Range": etag})
        self.assertEqual(response.status, STATUS_PARTIAL_CONTENT)
        self.assertEqual(response.headers["Content-Range"], "bytes 400-999/1000")
        self.assertEqual(partial + response.body, make_release(1))

    def test_stale_if_range_gets_the_whole_new_file(self):
        store = FileStore(make_release(1))
        _partial, old_etag = start_download(store)
        store.replace(make_release(2))
        response = handle_get(store, {"Range": "bytes=400-", "If-Range": old_etag})
        self.assertEqual(response.status, STATUS_OK)
        self.assertEqual(response.body, make_release(2))

    def test_resume_after_new_release_yields_a_valid_file(self):
        store = FileStore(make_release(1))
        partial, etag = start_download(store)
        store.replace(make_release(2))
        downloaded = resume_download(handle_get, store, partial, etag)
        self.assertEqual(checksum(downloaded), checksum(make_release(2)))

    def test_weak_etag_never_satisfies_if_range(self):
        store = FileStore(make_release(1))
        weak = "W/" + store.etag()
        response = handle_get(store, {"Range": "bytes=400-", "If-Range": weak})
        self.assertEqual(response.status, STATUS_OK)

    def test_http_date_in_if_range_is_treated_as_a_mismatch(self):
        self.assertFalse(if_range_matches("Sun, 15 Nov 2026 10:00:00 GMT", '"abc"'))

    def test_range_without_if_range_is_still_served(self):
        store = FileStore(make_release(1))
        response = handle_get(store, {"Range": "bytes=0-9"})
        self.assertEqual(response.status, STATUS_PARTIAL_CONTENT)
        self.assertEqual(response.body, make_release(1)[:10])


class RangeParsing(unittest.TestCase):
    def test_open_ended_range_runs_to_the_end(self):
        self.assertEqual(parse_range("bytes=400-", FILE_SIZE), (400, 999))

    def test_last_position_past_the_end_is_clamped(self):
        self.assertEqual(parse_range("bytes=990-5000", FILE_SIZE), (990, 999))

    def test_malformed_ranges_are_ignored(self):
        for header in ("bytes=5-2", "items=0-9", "bytes=-500", "bytes=0-9,20-29", "bytes=١-"):
            with self.subTest(header=header):
                self.assertIsNone(parse_range(header, FILE_SIZE))

    def test_huge_number_is_ignored_instead_of_crashing(self):
        self.assertIsNone(parse_range("bytes=" + "9" * 5000 + "-", FILE_SIZE))

    def test_malformed_range_gets_a_full_response(self):
        response = handle_get(FileStore(make_release(1)), {"Range": "bytes=5-2"})
        self.assertEqual(response.status, STATUS_OK)

    def test_range_starting_past_the_end_is_not_satisfiable(self):
        response = handle_get(FileStore(make_release(1)), {"Range": "bytes=1000-"})
        self.assertEqual(response.status, STATUS_RANGE_NOT_SATISFIABLE)
        self.assertEqual(response.headers["Content-Range"], "bytes */1000")

    def test_client_refuses_to_continue_on_unexpected_status(self):
        store = FileStore(make_release(1))
        with self.assertRaises(ValueError):
            resume_download(handle_get, store, make_release(1), store.etag())


if __name__ == "__main__":
    unittest.main()
