import unittest

from solution import (ACCESS_TOKEN_LIFETIME_SECONDS, REFRESH_EARLY_SECONDS, FakeClock,
                      InvalidGrant, OriginalTokenClient, RotatingAuthServer, TokenClient,
                      call_from_many_threads)

WORKER_THREADS = 8


def setup(client_class):
    clock = FakeClock()
    server = RotatingAuthServer(clock)
    client = client_class(server, clock)
    return clock, server, client


def expire_token(clock: FakeClock) -> None:
    clock.advance(ACCESS_TOKEN_LIFETIME_SECONDS + 1)


class ReproduceProblem(unittest.TestCase):
    def test_concurrent_refreshes_get_the_integration_revoked(self):
        clock, server, client = setup(OriginalTokenClient)
        expire_token(clock)
        tokens, failures = call_from_many_threads(client, WORKER_THREADS)
        self.assertTrue(server.revoked)
        self.assertEqual(server.refresh_calls, WORKER_THREADS)
        self.assertEqual(failures, WORKER_THREADS - 1)

    def test_even_the_thread_that_won_holds_a_dead_token(self):
        clock, server, client = setup(OriginalTokenClient)
        expire_token(clock)
        tokens, _ = call_from_many_threads(client, WORKER_THREADS)
        self.assertEqual(len(tokens), 1)
        self.assertFalse(server.accepts(tokens[0]))

    def test_single_thread_refresh_works_which_is_why_tests_passed(self):
        clock, server, client = setup(OriginalTokenClient)
        expire_token(clock)
        self.assertTrue(server.accepts(client.get_access_token()))


class FixSingleFlightRefresh(unittest.TestCase):
    def test_only_one_refresh_call_for_many_threads(self):
        clock, server, client = setup(TokenClient)
        expire_token(clock)
        call_from_many_threads(client, WORKER_THREADS)
        self.assertEqual(server.refresh_calls, 1)

    def test_every_thread_gets_the_same_working_token(self):
        clock, server, client = setup(TokenClient)
        expire_token(clock)
        tokens, failures = call_from_many_threads(client, WORKER_THREADS)
        self.assertEqual(failures, 0)
        self.assertEqual(set(tokens), {"access-2"})
        self.assertTrue(server.accepts("access-2"))
        self.assertFalse(server.revoked)

    def test_fresh_token_is_used_without_calling_the_server(self):
        clock, server, client = setup(TokenClient)
        self.assertEqual(client.get_access_token(), "access-1")
        self.assertEqual(server.refresh_calls, 0)

    def test_token_is_refreshed_shortly_before_it_expires(self):
        clock, server, client = setup(TokenClient)
        clock.advance(ACCESS_TOKEN_LIFETIME_SECONDS - REFRESH_EARLY_SECONDS)
        self.assertEqual(client.get_access_token(), "access-2")

    def test_each_expiry_causes_exactly_one_refresh(self):
        clock, server, client = setup(TokenClient)
        for _ in range(3):
            expire_token(clock)
            call_from_many_threads(client, WORKER_THREADS)
        self.assertEqual(server.refresh_calls, 3)
        self.assertEqual(client.get_access_token(), "access-4")

    def test_reusing_a_retired_refresh_token_is_still_rejected(self):
        clock, server, client = setup(TokenClient)
        expire_token(clock)
        client.get_access_token()
        with self.assertRaises(InvalidGrant):
            server.refresh("refresh-1")
        self.assertTrue(server.revoked)


if __name__ == "__main__":
    unittest.main()
