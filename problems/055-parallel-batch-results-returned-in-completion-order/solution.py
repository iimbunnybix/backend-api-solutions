"""Bulk price-update endpoint that returns results in the order items were sent.

The original endpoint updated each item of a batch in parallel and appended each
result to the response as soon as that item finished. Failed items finish
first (validation is fast, the database write is slow), so they moved to the
front of the list. Clients matched results to their request by position, so
they retried items that had succeeded and believed failed items were saved.
This version keeps one slot per request item, fills it by index, and echoes the
item's index and SKU in every result so clients never have to rely on order.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import partial
from typing import Any, Callable, Iterator, Protocol

# Upper bound on threads per request so one big batch cannot exhaust the host.
MAX_PARALLEL_UPDATES = 8

STATUS_OK = "ok"
STATUS_ERROR = "error"

# The result of updating one item, e.g. {"sku": "SKU-A", "status": "ok"}.
Outcome = dict[str, Any]

# Runs a list of tasks and yields (task index, outcome) in completion order.
Runner = Callable[[list[Callable[[], Outcome]]], Iterator[tuple[int, Outcome]]]


class PriceUpdateError(Exception):
    """An expected per-item failure (unknown SKU, invalid price)."""


class PricingService(Protocol):
    """Anything that can apply a single price change."""

    def update_price(self, sku: str, price_cents: int) -> None:
        """Apply one price change or raise PriceUpdateError."""


def _apply_one(service: PricingService, item: dict[str, Any]) -> Outcome:
    """Update one item and describe the outcome without raising.

    Catching the error here means one bad item cannot fail the whole batch.
    """
    try:
        service.update_price(item["sku"], item["price_cents"])
    except PriceUpdateError as error:
        return {"sku": item["sku"], "status": STATUS_ERROR, "error": str(error)}
    return {"sku": item["sku"], "status": STATUS_OK}


def run_in_parallel(tasks: list[Callable[[], Outcome]]) -> Iterator[tuple[int, Outcome]]:
    """Run tasks on a thread pool and yield (task index, outcome) as each finishes.

    Yield order is completion order, which changes from run to run.
    """
    with ThreadPoolExecutor(max_workers=MAX_PARALLEL_UPDATES) as pool:
        index_of_future = {pool.submit(task): index for index, task in enumerate(tasks)}
        for future in as_completed(index_of_future):
            yield index_of_future[future], future.result()


def _make_tasks(service: PricingService, items: list[dict[str, Any]]) -> list[Callable[[], Outcome]]:
    """One zero-argument task per request item, in request order."""
    return [partial(_apply_one, service, item) for item in items]


def naive_bulk_update(
    service: PricingService, items: list[dict[str, Any]], runner: Runner = run_in_parallel
) -> list[dict[str, Any]]:
    """Original handler: results are listed in the order items *finished*."""
    results: list[dict[str, Any]] = []
    for _index, outcome in runner(_make_tasks(service, items)):
        # The bug: the index and SKU are dropped, so position is the only
        # link back to the request, and position now means "finish order".
        results.append({"status": outcome["status"], "error": outcome.get("error")})
    return results


def bulk_update(
    service: PricingService, items: list[dict[str, Any]], runner: Runner = run_in_parallel
) -> list[dict[str, Any]]:
    """Fixed handler: result i always describes request item i.

    Items still run in parallel; only the way results are collected changes.
    Each result also carries its index and SKU so a client can check the
    pairing instead of trusting position.
    """
    slots: list[Outcome | None] = [None] * len(items)
    for index, outcome in runner(_make_tasks(service, items)):
        outcome["index"] = index
        # Put each outcome in its request slot, whenever it happens to finish.
        slots[index] = outcome
    return _require_all_filled(slots)


def _require_all_filled(slots: list[Outcome | None]) -> list[Outcome]:
    """Fail loudly if an item got no result, rather than shifting the others up."""
    results: list[Outcome] = []
    for index, outcome in enumerate(slots):
        if outcome is None:
            raise RuntimeError(f"no result for request item {index}")
        results.append(outcome)
    return results


def pair_by_position(items: list[dict[str, Any]], results: list[dict[str, Any]]) -> dict[str, str]:
    """What existing clients do: zip the request with the response."""
    statuses: dict[str, str] = {}
    for item, result in zip(items, results):
        statuses[item["sku"]] = result["status"]
    return statuses


def pair_by_identifier(items: list[dict[str, Any]], results: list[dict[str, Any]]) -> dict[str, str]:
    """What new clients should do: use the echoed index and check the SKU.

    Raises ValueError if the server ever pairs a result with the wrong item.
    """
    statuses: dict[str, str] = {}
    for result in results:
        item = items[result["index"]]
        if item["sku"] != result["sku"]:
            raise ValueError(f"result {result['index']} is for {result['sku']}, not {item['sku']}")
        statuses[item["sku"]] = result["status"]
    return statuses
