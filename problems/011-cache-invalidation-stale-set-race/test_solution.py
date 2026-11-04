import threading
import unittest

from solution import (Cache, Database, FakeClock, NaiveMenuService,
                      VersionedMenuService)

HOUR = 3600


def setup(service_cls):
    clock = FakeClock()
    db, cache = Database(), Cache(clock)
    db.write("dish-42", "available")
    return clock, db, cache, service_cls(db, cache)


class ReproduceStaleSet(unittest.TestCase):
    def test_slow_reader_writes_old_status_after_update(self):
        clock, db, cache, svc = setup(NaiveMenuService)
        # Reader misses, reads "available", then pauses while the kitchen marks it "sold_out".
        svc.get_item("dish-42", after_db_read=lambda: svc.update_item("dish-42", "sold_out"))
        self.assertEqual(db.read("dish-42")[0], "sold_out")
        self.assertEqual(svc.get_item("dish-42"), "available")   # cache now lies

    def test_stale_value_survives_for_the_whole_ttl(self):
        clock, db, cache, svc = setup(NaiveMenuService)
        svc.get_item("dish-42", after_db_read=lambda: svc.update_item("dish-42", "sold_out"))
        clock.advance(23 * HOUR)
        self.assertEqual(svc.get_item("dish-42"), "available")   # still stale after 23 h
        clock.advance(1 * HOUR)
        self.assertEqual(svc.get_item("dish-42"), "sold_out")   # only TTL expiry fixes it


class VersionedFix(unittest.TestCase):
    def test_slow_reader_cannot_overwrite_newer_write(self):
        clock, db, cache, svc = setup(VersionedMenuService)
        first = svc.get_item("dish-42", after_db_read=lambda: svc.update_item("dish-42", "sold_out"))
        self.assertEqual(first, "available")        # that request saw the row it read
        self.assertEqual(svc.get_item("dish-42"), "sold_out")
        self.assertEqual(cache.get("dish-42").value, "sold_out")

    def test_cache_is_refilled_after_tombstone(self):
        clock, db, cache, svc = setup(VersionedMenuService)
        svc.get_item("dish-42")
        svc.update_item("dish-42", "sold_out")
        self.assertTrue(cache.get("dish-42").tombstone)
        reads = db.reads
        self.assertEqual(svc.get_item("dish-42"), "sold_out")
        self.assertEqual(svc.get_item("dish-42"), "sold_out")
        self.assertEqual(db.reads, reads + 1)  # second read is a cache hit

    def test_out_of_order_invalidations_never_lower_version(self):
        clock, db, cache, _ = setup(VersionedMenuService)
        cache.invalidate("k", 5, 60)
        cache.invalidate("k", 3, 60)          # delayed invalidation from an older write
        self.assertEqual(cache.get("k").version, 5)
        self.assertFalse(cache.set_if_newer("k", "v4", 4, 100))
        self.assertTrue(cache.set_if_newer("k", "v5", 5, 100))
        self.assertFalse(cache.set_if_newer("k", "v5-again", 5, 100))
        self.assertTrue(cache.set_if_newer("k", "v6", 6, 100))

    def test_two_updates_during_one_slow_read(self):
        clock, db, cache, svc = setup(VersionedMenuService)

        def two_updates():
            svc.update_item("dish-42", "sold_out")
            svc.update_item("dish-42", "hidden")
        svc.get_item("dish-42", after_db_read=two_updates)
        self.assertEqual(svc.get_item("dish-42"), "hidden")

    def test_reader_slower_than_tombstone_ttl_is_the_documented_limit(self):
        clock, db, cache, svc = setup(VersionedMenuService)

        def update_then_stall():
            svc.update_item("dish-42", "sold_out")
            clock.advance(61)                   # longer than tombstone_ttl=60
        svc.get_item("dish-42", after_db_read=update_then_stall)
        self.assertEqual(svc.get_item("dish-42"), "available")  # why tombstone_ttl must be generous

    def test_concurrent_readers_and_writers_converge(self):
        clock = FakeClock()
        db, cache = Database(), Cache(clock)
        db.write("k", 0)
        svc = VersionedMenuService(db, cache)
        barrier = threading.Barrier(9)

        def reader():
            barrier.wait()
            for _ in range(200):
                svc.get_item("k")

        def writer():
            barrier.wait()
            for i in range(1, 201):
                svc.update_item("k", i)
        threads = [threading.Thread(target=reader) for _ in range(8)] + [threading.Thread(target=writer)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(svc.get_item("k"), db.read("k")[0])


if __name__ == "__main__":
    unittest.main()
