"""The orders API reported a page count that was one page short.

``total_pages`` was computed with floor division (``total // page_size``), so
the final, partly filled page was never counted. Integrations that loop
``for page in range(1, total_pages + 1)`` silently skipped the newest orders,
and customers with fewer orders than one page got ``total_pages = 0`` and an
empty export. The fix rounds the page count up with integer arithmetic,
validates the page size, and also returns ``has_next`` so clients do not
have to do the division themselves.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

DEFAULT_PAGE_SIZE = 100
MAX_PAGE_SIZE = 500
FIRST_PAGE = 1

PageCounter = Callable[[int, int], int]     # (total_items, page_size) -> pages


class InvalidPageRequest(ValueError):
    """The client asked for a page that cannot exist (HTTP 400)."""


@dataclass(frozen=True)
class OrdersPage:
    """One page of the ``GET /orders`` response."""

    items: list[int]
    page: int
    page_size: int
    total_items: int
    total_pages: int
    has_next: bool


# ---------------------------------------------------------------- original
def naive_total_pages(total_items: int, page_size: int) -> int:
    """Original: floor division drops the last, partly filled page."""
    return total_items // page_size


# ---------------------------------------------------------------- the fix
def total_pages(total_items: int, page_size: int) -> int:
    """Number of pages needed to show ``total_items``, rounding up.

    ``(a + b - 1) // b`` is ceiling division on integers. We avoid
    ``math.ceil(a / b)`` because the float division loses precision once
    counts pass 2**53.
    """
    if page_size < 1:
        raise InvalidPageRequest("page_size must be at least 1")
    if total_items < 0:
        raise ValueError("total_items cannot be negative")
    return (total_items + page_size - 1) // page_size


def validate_page_request(page: int, page_size: int) -> None:
    """Reject page numbers and sizes the API does not support."""
    if page < FIRST_PAGE:
        raise InvalidPageRequest("page numbers start at 1")
    if page_size < 1 or page_size > MAX_PAGE_SIZE:
        raise InvalidPageRequest(f"page_size must be between 1 and {MAX_PAGE_SIZE}")


def list_orders(order_ids: list[int], page: int,
                page_size: int = DEFAULT_PAGE_SIZE,
                count_pages: PageCounter = total_pages) -> OrdersPage:
    """Serve one page of orders together with paging metadata.

    ``count_pages`` is the page-count function, so tests can serve the same
    data with the original and the fixed calculation.
    """
    validate_page_request(page, page_size)
    start = (page - 1) * page_size
    items = order_ids[start:start + page_size]
    pages = count_pages(len(order_ids), page_size)
    # has_next comes from the data itself, not from the page count, so a
    # client that only follows has_next can never stop early.
    has_next = start + page_size < len(order_ids)
    return OrdersPage(items, page, page_size, len(order_ids), pages, has_next)


# ---------------------------------------------------------------- clients
def export_by_page_count(order_ids: list[int], page_size: int,
                         count_pages: PageCounter = total_pages) -> list[int]:
    """How the partner integration exported: loop up to ``total_pages``."""
    first = list_orders(order_ids, FIRST_PAGE, page_size, count_pages)
    exported: list[int] = []
    for page in range(FIRST_PAGE, first.total_pages + 1):
        exported.extend(list_orders(order_ids, page, page_size, count_pages).items)
    return exported


def export_by_has_next(order_ids: list[int], page_size: int) -> list[int]:
    """Recommended client loop: keep going while the server says there is more."""
    exported: list[int] = []
    page = FIRST_PAGE
    while True:
        response = list_orders(order_ids, page, page_size)
        exported.extend(response.items)
        if not response.has_next:
            return exported
        page += 1
