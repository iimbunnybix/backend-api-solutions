"""Bulk product import that no longer throws away a whole file because of one bad row.

The original endpoint wrapped the entire upload in one transaction and stopped at
the first invalid row: one typo in row 4,812 rolled back 4,811 good rows and the
response only said "row 4812 invalid", so merchants fixed one row at a time and
re-uploaded. This version validates and saves each row on its own, reports a
result for every row (HTTP 207 Multi-Status) and caps the batch size so one
request cannot hold the database for minutes.
"""
from __future__ import annotations

from dataclasses import dataclass, field

MAX_BATCH_SIZE = 1_000
MAX_NAME_LENGTH = 200
NOT_AN_OBJECT_ERROR = "row must be a JSON object"
HTTP_OK = 200
HTTP_MULTI_STATUS = 207
HTTP_BAD_REQUEST = 400
HTTP_PAYLOAD_TOO_LARGE = 413

STATUS_CREATED = "created"
STATUS_INVALID = "invalid"
STATUS_DUPLICATE = "duplicate"


class ValidationError(Exception):
    """A row breaks a business rule; the message is shown to the merchant."""


class ProductDatabase:
    """Tiny in-memory table with transactions, standing in for SQL."""

    def __init__(self) -> None:
        self.products: dict[str, dict] = {}
        self._pending: dict[str, dict] | None = None

    def begin(self) -> None:
        """Start a transaction: writes go to a pending copy until commit."""
        self._pending = dict(self.products)

    def insert(self, product: dict) -> None:
        """Insert a row inside the open transaction; SKU is the primary key."""
        if self._pending is None:
            raise RuntimeError("insert outside a transaction")
        if product["sku"] in self._pending:
            raise ValidationError(f"sku {product['sku']} already exists")
        self._pending[product["sku"]] = product

    def commit(self) -> None:
        """Make the pending writes visible."""
        self.products = self._pending or {}
        self._pending = None

    def rollback(self) -> None:
        """Discard the pending writes."""
        self._pending = None


@dataclass
class RowResult:
    index: int
    sku: str | None
    status: str
    errors: list[str] = field(default_factory=list)


def validate_row(row: object) -> list[str]:
    """Return every problem with a row (not just the first), so one upload fixes all."""
    # A spreadsheet export can turn a blank line into null; such a row is invalid,
    # not a reason to crash the whole request after earlier rows were saved.
    if not isinstance(row, dict):
        return [NOT_AN_OBJECT_ERROR]
    errors = []
    sku = row.get("sku")
    if not isinstance(sku, str) or not sku.strip():
        errors.append("sku is required")
    name = row.get("name")
    if not isinstance(name, str) or not name.strip():
        errors.append("name is required")
    elif len(name) > MAX_NAME_LENGTH:
        errors.append(f"name longer than {MAX_NAME_LENGTH} characters")
    price = row.get("price_cents")
    # bool is a subclass of int in Python; True must not become a 1-cent price.
    if not isinstance(price, int) or isinstance(price, bool) or price <= 0:
        errors.append("price_cents must be a positive integer")
    return errors


def naive_import(database: ProductDatabase, rows: list[dict]) -> tuple[int, dict]:
    """Original endpoint: one transaction, abort on the first bad row."""
    database.begin()
    for index, row in enumerate(rows):
        errors = validate_row(row)
        try:
            if errors:
                raise ValidationError("; ".join(errors))
            database.insert(row)
        except ValidationError as error:
            database.rollback()
            return HTTP_BAD_REQUEST, {"error": f"row {index} invalid: {error}"}
    database.commit()
    return HTTP_OK, {"imported": len(rows)}


def import_one_row(database: ProductDatabase, index: int, row: object) -> RowResult:
    """Validate and save a single row in its own small transaction."""
    sku = None
    if isinstance(row, dict) and isinstance(row.get("sku"), str):
        sku = row["sku"]
    errors = validate_row(row)
    if errors:
        return RowResult(index, sku, STATUS_INVALID, errors)
    database.begin()
    try:
        database.insert(row)
    except ValidationError as error:
        database.rollback()
        return RowResult(index, sku, STATUS_DUPLICATE, [str(error)])
    database.commit()
    return RowResult(index, sku, STATUS_CREATED)


def summarise(results: list[RowResult]) -> dict:
    """Build the response body: counts plus one entry per row, in input order."""
    counts = {STATUS_CREATED: 0, STATUS_INVALID: 0, STATUS_DUPLICATE: 0}
    for result in results:
        counts[result.status] += 1
    return {"counts": counts, "results": [vars(result) for result in results]}


def bulk_import(database: ProductDatabase, rows: list[dict]) -> tuple[int, dict]:
    """Fixed endpoint: per-row results, partial success, bounded batch size."""
    # Reject oversized batches before doing any work, so the client splits the file.
    if len(rows) > MAX_BATCH_SIZE:
        return HTTP_PAYLOAD_TOO_LARGE, {"error": f"at most {MAX_BATCH_SIZE} rows per request"}
    results = []
    for index, row in enumerate(rows):
        results.append(import_one_row(database, index, row))
    body = summarise(results)
    # 207 tells the client to read per-row statuses; 200 only when everything worked.
    all_created = body["counts"][STATUS_CREATED] == len(rows)
    return (HTTP_OK if all_created else HTTP_MULTI_STATUS), body


def make_rows(count: int, bad_indexes: set[int]) -> list[dict]:
    """Generate a merchant upload with some rows missing their price."""
    rows = []
    for index in range(count):
        row = {"sku": f"SKU-{index:05d}", "name": f"Item {index}", "price_cents": 100 + index}
        if index in bad_indexes:
            row["price_cents"] = None
        rows.append(row)
    return rows
