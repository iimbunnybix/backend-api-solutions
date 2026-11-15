import unittest
from functools import partial
from typing import Callable, Iterator

from solution import (
    MAX_PARALLEL_UPDATES,
    STATUS_ERROR,
    STATUS_OK,
    PriceUpdateError,
    bulk_update,
    naive_bulk_update,
    pair_by_identifier,
    pair_by_position,
    run_in_parallel,
)


class FakePricingService:
    """In-memory pricing service; unknown SKUs fail like a validation error."""

    def __init__(self, unknown_skus: set[str] = frozenset()):
        self.unknown_skus = set(unknown_skus)
        self.saved_prices: dict[str, int] = {}

    def update_price(self, sku: str, price_cents: int) -> None:
        if sku in self.unknown_skus:
            raise PriceUpdateError(f"unknown SKU {sku}")
        self.saved_prices[sku] = price_cents


class ScriptedRunner:
    """Stands in for the thread pool: finishes tasks in a chosen order.

    This makes "completion order" deterministic so the bug reproduces on
    every run instead of only when thread timing happens to line up.
    """

    def __init__(self, finish_order: list[int]):
        self.finish_order = finish_order

    def __call__(self, tasks: list[Callable[[], dict]]) -> Iterator[tuple[int, dict]]:
        for index in self.finish_order:
            yield index, tasks[index]()


def echo(number: int) -> dict:
    return {"value": number}


def make_items(*skus: str) -> list[dict]:
    return [{"sku": sku, "price_cents": 1000 + index} for index, sku in enumerate(skus)]


# The incident: SKU-C is unknown and fails validation instantly, so it finishes
# first; SKU-B's database write is the slowest.
REQUEST = make_items("SKU-A", "SKU-B", "SKU-C", "SKU-D")
FAILING_SKU = "SKU-C"
INCIDENT_FINISH_ORDER = ScriptedRunner([2, 0, 3, 1])


def incident_service() -> FakePricingService:
    return FakePricingService(unknown_skus={FAILING_SKU})


class ReproduceProblem(unittest.TestCase):
    def test_naive_results_are_in_completion_order(self):
        results = naive_bulk_update(incident_service(), REQUEST, INCIDENT_FINISH_ORDER)
        self.assertEqual(results[0]["status"], STATUS_ERROR)

    def test_client_blames_the_first_item_for_the_failure(self):
        results = naive_bulk_update(incident_service(), REQUEST, INCIDENT_FINISH_ORDER)
        self.assertEqual(pair_by_position(REQUEST, results)["SKU-A"], STATUS_ERROR)

    def test_client_believes_the_failed_item_was_saved(self):
        service = incident_service()
        results = naive_bulk_update(service, REQUEST, INCIDENT_FINISH_ORDER)
        self.assertEqual(pair_by_position(REQUEST, results)[FAILING_SKU], STATUS_OK)
        self.assertNotIn(FAILING_SKU, service.saved_prices)


class FixResultsInRequestOrder(unittest.TestCase):
    def test_result_i_describes_request_item_i(self):
        results = bulk_update(incident_service(), REQUEST, INCIDENT_FINISH_ORDER)
        self.assertEqual([result["sku"] for result in results], [item["sku"] for item in REQUEST])

    def test_position_based_client_now_sees_the_right_failure(self):
        results = bulk_update(incident_service(), REQUEST, INCIDENT_FINISH_ORDER)
        statuses = pair_by_position(REQUEST, results)
        self.assertEqual(statuses[FAILING_SKU], STATUS_ERROR)
        self.assertEqual(statuses["SKU-A"], STATUS_OK)

    def test_every_result_echoes_its_index(self):
        results = bulk_update(incident_service(), REQUEST, INCIDENT_FINISH_ORDER)
        self.assertEqual([result["index"] for result in results], [0, 1, 2, 3])

    def test_failed_result_carries_the_error_message(self):
        results = bulk_update(incident_service(), REQUEST, INCIDENT_FINISH_ORDER)
        self.assertEqual(results[2]["error"], "unknown SKU SKU-C")

    def test_one_failed_item_does_not_stop_the_others(self):
        service = incident_service()
        bulk_update(service, REQUEST, INCIDENT_FINISH_ORDER)
        self.assertEqual(sorted(service.saved_prices), ["SKU-A", "SKU-B", "SKU-D"])

    def test_identifier_based_client_pairs_correctly(self):
        results = bulk_update(incident_service(), REQUEST, INCIDENT_FINISH_ORDER)
        self.assertEqual(pair_by_identifier(REQUEST, results),
                         {"SKU-A": STATUS_OK, "SKU-B": STATUS_OK,
                          "SKU-C": STATUS_ERROR, "SKU-D": STATUS_OK})


class RealThreadPool(unittest.TestCase):
    def test_real_thread_pool_runs_every_task_once(self):
        tasks = [partial(echo, number) for number in range(20)]
        indexes = sorted(index for index, _outcome in run_in_parallel(tasks))
        self.assertEqual(indexes, list(range(20)))

    def test_fix_keeps_request_order_with_more_items_than_threads(self):
        skus = [f"SKU-{number:02d}" for number in range(MAX_PARALLEL_UPDATES * 3)]
        results = bulk_update(FakePricingService(), make_items(*skus))
        self.assertEqual([result["sku"] for result in results], skus)


class EdgeCases(unittest.TestCase):
    def test_empty_batch_returns_empty_list(self):
        self.assertEqual(bulk_update(FakePricingService(), []), [])

    def test_duplicate_skus_are_told_apart_by_index(self):
        items = make_items("SKU-A", "SKU-A")
        results = bulk_update(FakePricingService(), items, ScriptedRunner([1, 0]))
        self.assertEqual([result["index"] for result in results], [0, 1])
        self.assertEqual([result["status"] for result in results], [STATUS_OK, STATUS_OK])

    def test_missing_result_raises_instead_of_shifting_others(self):
        with self.assertRaises(RuntimeError):
            bulk_update(FakePricingService(), make_items("SKU-A", "SKU-B"), ScriptedRunner([1]))

    def test_identifier_check_catches_a_mispaired_result(self):
        wrong = [{"index": 0, "sku": "SKU-B", "status": STATUS_OK}]
        with self.assertRaises(ValueError):
            pair_by_identifier(make_items("SKU-A", "SKU-B"), wrong)


if __name__ == "__main__":
    unittest.main()
