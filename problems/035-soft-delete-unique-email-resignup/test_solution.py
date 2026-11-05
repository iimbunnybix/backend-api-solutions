import unittest

from solution import (EmailTaken, insert_account, list_accounts, live_email_exists,
                      naive_list_accounts, open_constraint_dropped_store, open_fixed_store,
                      open_original_store, restore, sign_up, soft_delete)

EMAIL = "asha@example.com"
DELETED_AT = "2026-03-01T10:00:00Z"
OPEN_STORES = []


def tearDownModule():
    """Close every in-memory database the tests opened."""
    for store in OPEN_STORES:
        store.close()


def store_with_deleted_account(open_store):
    """A store where EMAIL signed up and then deleted the account. Returns (store, old_id)."""
    store = open_store()
    OPEN_STORES.append(store)
    old_id = sign_up(store, EMAIL, "Asha")
    soft_delete(store, old_id, DELETED_AT)
    return store, old_id


def race_two_sign_ups(open_store):
    """Two requests for the same new email both pass the check, then both insert."""
    store = open_store()
    OPEN_STORES.append(store)
    first_check_free = not live_email_exists(store, EMAIL)
    second_check_free = not live_email_exists(store, EMAIL)
    assert first_check_free and second_check_free
    insert_account(store, EMAIL, "Asha (tab 1)")
    insert_account(store, EMAIL, "Asha (tab 2)")
    return store


def live_count(store, email):
    """Number of live accounts using ``email``."""
    return sum(1 for account in list_accounts(store) if account.email == email)


class ReproduceProblem(unittest.TestCase):
    def test_deleted_user_cannot_sign_up_again(self):
        store, _ = store_with_deleted_account(open_original_store)
        with self.assertRaises(EmailTaken):
            sign_up(store, EMAIL, "Asha again")

    def test_deleted_account_still_appears_in_listing(self):
        store, old_id = store_with_deleted_account(open_original_store)
        self.assertEqual([a.id for a in naive_list_accounts(store)], [old_id])

    def test_dropping_the_constraint_allows_duplicate_live_accounts(self):
        store = race_two_sign_ups(open_constraint_dropped_store)
        self.assertEqual(live_count(store, EMAIL), 2)


class FixLiveOnlyUniqueness(unittest.TestCase):
    def test_deleted_user_can_sign_up_again(self):
        store, old_id = store_with_deleted_account(open_fixed_store)
        new_id = sign_up(store, EMAIL, "Asha again")
        self.assertNotEqual(new_id, old_id)
        self.assertEqual(live_count(store, EMAIL), 1)

    def test_second_live_account_with_same_email_is_rejected(self):
        store = open_fixed_store()
        OPEN_STORES.append(store)
        sign_up(store, EMAIL, "Asha")
        with self.assertRaises(EmailTaken):
            sign_up(store, EMAIL, "Someone else")

    def test_concurrent_sign_ups_create_only_one_live_account(self):
        with self.assertRaises(EmailTaken):
            race_two_sign_ups(open_fixed_store)

    def test_email_can_be_deleted_and_reused_several_times(self):
        store, _ = store_with_deleted_account(open_fixed_store)
        second_id = sign_up(store, EMAIL, "Asha 2")
        soft_delete(store, second_id, "2026-06-01T10:00:00Z")
        sign_up(store, EMAIL, "Asha 3")
        all_rows = list_accounts(store, include_deleted=True)
        self.assertEqual(len(all_rows), 3)
        self.assertEqual(live_count(store, EMAIL), 1)

    def test_restoring_old_account_fails_when_email_was_reused(self):
        store, old_id = store_with_deleted_account(open_fixed_store)
        sign_up(store, EMAIL, "Asha again")
        with self.assertRaises(EmailTaken):
            restore(store, old_id)
        self.assertEqual(live_count(store, EMAIL), 1)

    def test_restoring_old_account_works_when_email_is_free(self):
        store, old_id = store_with_deleted_account(open_fixed_store)
        restore(store, old_id)
        self.assertEqual([a.id for a in list_accounts(store)], [old_id])


class FixListingExcludesDeleted(unittest.TestCase):
    def test_listing_hides_deleted_accounts_by_default(self):
        store, _ = store_with_deleted_account(open_fixed_store)
        sign_up(store, "ben@example.com", "Ben")
        self.assertEqual([a.email for a in list_accounts(store)], ["ben@example.com"])

    def test_admin_listing_can_include_deleted_accounts(self):
        store, old_id = store_with_deleted_account(open_fixed_store)
        accounts = list_accounts(store, include_deleted=True)
        self.assertEqual([(a.id, a.deleted_at) for a in accounts], [(old_id, DELETED_AT)])

    def test_soft_delete_twice_keeps_first_timestamp(self):
        store, old_id = store_with_deleted_account(open_fixed_store)
        soft_delete(store, old_id, "2026-09-09T00:00:00Z")
        account = list_accounts(store, include_deleted=True)[0]
        self.assertEqual(account.deleted_at, DELETED_AT)


if __name__ == "__main__":
    unittest.main()
