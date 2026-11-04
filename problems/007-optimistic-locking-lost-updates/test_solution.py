import threading
import unittest

from solution import (ConflictRetriesExhausted, NotFound, PreconditionFailed,
                      PreconditionRequired, ProductStore, parse_etag)


def new_store():
    s = ProductStore()
    s.create("sku-1", "Desk lamp", 2500, 40)
    return s


class ReproduceLostUpdate(unittest.TestCase):
    def test_last_writer_silently_overwrites_other_edit(self):
        s = new_store()
        alice, _ = s.get("sku-1")        # merchandiser opens the edit form
        bob, _ = s.get("sku-1")          # stock-sync job reads the same row
        alice["price_cents"] = 1999       # price cut for the sale
        s.put_unconditional("sku-1", alice)
        bob["stock"] = 12                 # warehouse count
        s.put_unconditional("sku-1", bob)
        final, _ = s.get("sku-1")
        self.assertEqual(final["stock"], 12)
        self.assertEqual(final["price_cents"], 2500)  # Alice's price cut is gone


class IfMatch(unittest.TestCase):
    def test_second_writer_gets_412_instead_of_overwriting(self):
        s = new_store()
        alice, etag_a = s.get("sku-1")
        bob, etag_b = s.get("sku-1")
        alice["price_cents"] = 1999
        new_etag = s.put("sku-1", alice, if_match=etag_a)
        bob["stock"] = 12
        with self.assertRaises(PreconditionFailed) as ctx:
            s.put("sku-1", bob, if_match=etag_b)
        self.assertEqual(ctx.exception.current_etag, new_etag)
        final, _ = s.get("sku-1")
        self.assertEqual((final["price_cents"], final["stock"]), (1999, 40))

    def test_client_can_reload_and_reapply(self):
        s = new_store()
        _, stale = s.get("sku-1")
        s.update_with_retry("sku-1", lambda r: {**r, "price_cents": 1999})
        with self.assertRaises(PreconditionFailed):
            s.put("sku-1", {"name": "Desk lamp", "price_cents": 2500, "stock": 12}, stale)
        fresh, etag = s.get("sku-1")
        s.put("sku-1", {**fresh, "stock": 12}, etag)
        final, _ = s.get("sku-1")
        self.assertEqual((final["price_cents"], final["stock"]), (1999, 12))

    def test_missing_if_match_is_rejected(self):
        s = new_store()
        rec, _ = s.get("sku-1")
        for header in (None, ""):
            with self.assertRaises(PreconditionRequired):
                s.put("sku-1", rec, if_match=header)

    def test_malformed_etag_is_treated_as_mismatch(self):
        s = new_store()
        rec, _ = s.get("sku-1")
        with self.assertRaises(PreconditionFailed):
            s.put("sku-1", rec, if_match="garbage")

    def test_weak_etag_accepted(self):
        s = new_store()
        rec, etag = s.get("sku-1")
        s.put("sku-1", {**rec, "stock": 1}, if_match="W/" + etag)
        self.assertEqual(s.get("sku-1")[0]["stock"], 1)

    def test_unknown_product_is_404(self):
        s = new_store()
        with self.assertRaises(NotFound):
            s.put("nope", {"name": "x", "price_cents": 1, "stock": 1}, if_match='"v1"')
        with self.assertRaises(NotFound):
            s.get("nope")

    def test_partial_body_rejected(self):
        s = new_store()
        _, etag = s.get("sku-1")
        with self.assertRaises(ValueError):
            s.put("sku-1", {"stock": 3}, if_match=etag)

    def test_parse_etag(self):
        self.assertEqual(parse_etag('"v12"'), 12)
        self.assertEqual(parse_etag('W/"v3"'), 3)
        for bad in ('v3', '"3"', '"vx"', ''):
            with self.assertRaises(ValueError):
                parse_etag(bad)


class ConcurrentWriters(unittest.TestCase):
    def test_no_decrement_is_lost_under_contention(self):
        s = new_store()
        start = threading.Barrier(8)

        def worker():
            start.wait()
            for _ in range(5):
                s.update_with_retry("sku-1", lambda r: {**r, "stock": r["stock"] - 1},
                                    max_attempts=1000)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        rec, etag = s.get("sku-1")
        self.assertEqual(rec["stock"], 0)          # 40 - 8*5, exactly
        self.assertEqual(etag, '"v41"')            # one version bump per decrement

    def test_retries_give_up_when_always_conflicting(self):
        s = new_store()

        def meddling_change(r):
            # Another writer sneaks in between our read and our write, every time.
            s.update_with_retry("sku-1", lambda x: {**x, "stock": x["stock"] + 1})
            return {**r, "price_cents": 1}

        with self.assertRaises(ConflictRetriesExhausted):
            s.update_with_retry("sku-1", meddling_change, max_attempts=3)
        self.assertEqual(s.get("sku-1")[0]["price_cents"], 2500)


if __name__ == "__main__":
    unittest.main()
