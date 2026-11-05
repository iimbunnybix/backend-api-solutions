import asyncio
import unittest
from concurrent.futures import ThreadPoolExecutor

from solution import (ANONYMOUS_GREETING, Request, current_user,
                      make_greeting_handler, naive_current_user,
                      naive_middleware, request_scope, safe_middleware)

ALICE = Request("/home", session_user="alice")
GUEST = Request("/home")


def run_on_one_reused_thread(middleware, handler, requests):
    """Serve requests one after another on a single pooled worker thread."""
    with ThreadPoolExecutor(max_workers=1) as pool:
        return [pool.submit(middleware, request, handler).result() for request in requests]


class ReproduceProblem(unittest.TestCase):
    def test_guest_on_reused_thread_is_greeted_as_previous_user(self):
        handler = make_greeting_handler(naive_current_user)
        responses = run_on_one_reused_thread(naive_middleware, handler, [ALICE, GUEST])
        self.assertEqual(responses, ["Hello, alice", "Hello, alice"])

    def test_interleaved_async_requests_read_each_others_user(self):
        seen = {}

        async def handle(name, user):
            naive_middleware(Request("/home", session_user=user), lambda request: "")
            await asyncio.sleep(0)            # another task runs here
            seen[name] = naive_current_user()

        async def main():
            await asyncio.gather(handle("first", "alice"), handle("second", "bob"))

        asyncio.run(main())
        self.assertEqual(seen["first"], "bob")   # alice's request now thinks it is bob


class FixRequestScopedContext(unittest.TestCase):
    def test_guest_on_reused_thread_is_anonymous(self):
        handler = make_greeting_handler(current_user)
        responses = run_on_one_reused_thread(safe_middleware, handler, [ALICE, GUEST])
        self.assertEqual(responses, ["Hello, alice", ANONYMOUS_GREETING])

    def test_user_is_cleared_after_handler_raises(self):
        def failing_handler(request):
            raise RuntimeError("boom")

        with ThreadPoolExecutor(max_workers=1) as pool:
            with self.assertRaises(RuntimeError):
                pool.submit(safe_middleware, ALICE, failing_handler).result()
            after = pool.submit(current_user).result()
        self.assertIsNone(after)

    def test_interleaved_async_requests_keep_their_own_user(self):
        seen = {}

        async def handle(name, user):
            with request_scope(user):
                await asyncio.sleep(0)
                seen[name] = current_user()

        async def main():
            await asyncio.gather(handle("first", "alice"), handle("second", "bob"))

        asyncio.run(main())
        self.assertEqual(seen, {"first": "alice", "second": "bob"})

    def test_nested_scope_restores_outer_user(self):
        with request_scope("alice"):
            with request_scope("support-impersonation"):
                self.assertEqual(current_user(), "support-impersonation")
            self.assertEqual(current_user(), "alice")
        self.assertIsNone(current_user())

    def test_many_alternating_requests_never_leak(self):
        handler = make_greeting_handler(current_user)
        requests = [ALICE, GUEST] * 50
        responses = run_on_one_reused_thread(safe_middleware, handler, requests)
        self.assertEqual(responses.count(ANONYMOUS_GREETING), 50)
        self.assertEqual(responses.count("Hello, alice"), 50)


if __name__ == "__main__":
    unittest.main()
