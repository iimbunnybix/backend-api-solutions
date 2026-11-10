import unittest

from solution import (DEFAULT_STATEMENT_TIMEOUT_MS, TENANT_SETTING, TIMEOUT_SETTING,
                      UNLIMITED_TIMEOUT_MS, NO_TENANT, PaymentDeclined, Product,
                      handle_failing_checkout, handle_monthly_report,
                      handle_public_catalogue, handle_tenant_dashboard, naive_pool,
                      resetting_pool)

PRODUCTS = [
    Product("public-mug", tenant_id="acme", is_public=True),
    Product("acme-unreleased-phone", tenant_id="acme", is_public=False),
    Product("globex-draft-pricing", tenant_id="globex", is_public=False),
]
PUBLIC_ONLY = ["public-mug"]


def run_failing_checkout(pool) -> None:
    """Run the checkout handler, swallowing the expected decline."""
    try:
        handle_failing_checkout(pool)
    except PaymentDeclined:
        pass


class ReproduceProblem(unittest.TestCase):
    def test_anonymous_request_sees_previous_tenants_private_rows(self):
        pool = naive_pool(PRODUCTS)
        handle_tenant_dashboard(pool, "acme")
        self.assertIn("acme-unreleased-phone", handle_public_catalogue(pool))

    def test_report_timeout_override_leaks_to_next_request(self):
        pool = naive_pool(PRODUCTS)
        handle_monthly_report(pool)
        connection = pool.acquire()
        self.assertEqual(connection.settings[TIMEOUT_SETTING], UNLIMITED_TIMEOUT_MS)

    def test_failed_transaction_is_handed_to_next_borrower(self):
        pool = naive_pool(PRODUCTS)
        run_failing_checkout(pool)
        self.assertTrue(pool.acquire().in_transaction)


class FixByResettingOnRelease(unittest.TestCase):
    def test_anonymous_request_after_tenant_request_sees_only_public_rows(self):
        pool = resetting_pool(PRODUCTS)
        handle_tenant_dashboard(pool, "acme")
        self.assertEqual(handle_public_catalogue(pool), PUBLIC_ONLY)

    def test_tenant_still_sees_its_own_private_rows(self):
        pool = resetting_pool(PRODUCTS)
        visible = handle_tenant_dashboard(pool, "globex")
        self.assertEqual(visible, ["public-mug", "globex-draft-pricing"])

    def test_tenants_do_not_see_each_other_on_a_reused_connection(self):
        pool = resetting_pool(PRODUCTS)
        handle_tenant_dashboard(pool, "acme")
        self.assertNotIn("acme-unreleased-phone", handle_tenant_dashboard(pool, "globex"))

    def test_timeout_is_back_to_default_after_report(self):
        pool = resetting_pool(PRODUCTS)
        handle_monthly_report(pool)
        connection = pool.acquire()
        self.assertEqual(connection.settings[TIMEOUT_SETTING], DEFAULT_STATEMENT_TIMEOUT_MS)

    def test_failed_transaction_is_rolled_back_before_reuse(self):
        pool = resetting_pool(PRODUCTS)
        run_failing_checkout(pool)
        connection = pool.acquire()
        self.assertFalse(connection.in_transaction)
        self.assertEqual(connection.settings[TENANT_SETTING], NO_TENANT)

    def test_connection_is_returned_even_when_handler_raises(self):
        pool = resetting_pool(PRODUCTS)
        run_failing_checkout(pool)
        self.assertEqual(len(pool.idle), 1)

    def test_connections_are_still_reused_not_reopened(self):
        pool = resetting_pool(PRODUCTS)
        for _ in range(50):
            handle_tenant_dashboard(pool, "acme")
            handle_public_catalogue(pool)
        self.assertEqual(pool.created_count, 1)


if __name__ == "__main__":
    unittest.main()
