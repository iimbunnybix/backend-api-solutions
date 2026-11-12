"""Warn about a 32-bit auto-increment primary key long before it runs out.

The orders table used a signed 32-bit INT id, which tops out at 2,147,483,647.
The team watched the row count, but rolled-back inserts and bulk inserts that
reserve ids in power-of-two blocks burn ids without adding rows, so the id
counter reached the limit while the table looked far from full and every
insert started failing. The fix monitors the id counter itself, forecasts the
days left from the recent allocation rate, alerts with a lead time big enough
for a migration, and checks that the key and every column referencing it are
64-bit.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass

INT32_MAX = 2**31 - 1
# The original monitor: alert when the table holds 80% of the possible rows.
ROW_COUNT_ALERT_FRACTION = 0.8
# A BIGINT migration of a large table takes weeks; alert this many days ahead.
ALERT_LEAD_DAYS = 90
# Forecast from the recent rate, so traffic growth is not averaged away.
RATE_WINDOW_DAYS = 7
SIXTY_FOUR_BIT_TYPES = frozenset({"bigint", "int8"})


class IdSpaceExhausted(Exception):
    """Raised when an insert needs an id above the column's maximum."""


class IdAllocator:
    """Auto-increment counter: hands out ids and never gives them back."""

    def __init__(self, next_id: int, max_id: int = INT32_MAX):
        self.next_id = next_id
        self.max_id = max_id

    def allocate(self, count: int) -> int:
        """Reserve `count` consecutive ids and return the first one."""
        if self.next_id + count - 1 > self.max_id:
            raise IdSpaceExhausted(f"need {count} ids, only {self.max_id - self.next_id + 1} left")
        first_id = self.next_id
        self.next_id += count
        return first_id

    @property
    def last_allocated(self) -> int:
        """Highest id handed out so far (what AUTO_INCREMENT / last_value shows)."""
        return self.next_id - 1


def next_power_of_two(number: int) -> int:
    """Smallest power of two >= number (how bulk inserts grow their id reservation)."""
    power = 1
    while power < number:
        power *= 2
    return power


class OrdersTable:
    """Rows and the id counter behind them; the two drift apart over time."""

    def __init__(self, allocator: IdAllocator, row_count: int):
        self.allocator = allocator
        self.row_count = row_count

    def insert_orders(self, count: int, committed: bool) -> None:
        """Single-row inserts; a rollback keeps the ids burned but adds no rows."""
        self.allocator.allocate(count)
        if committed:
            self.row_count += count

    def bulk_insert(self, rows: int) -> None:
        """Bulk insert of unknown size: ids are reserved in doubling blocks."""
        self.allocator.allocate(next_power_of_two(rows))
        self.row_count += rows


@dataclass(frozen=True)
class DailyWorkload:
    committed_orders: int = 4_000_000
    rolled_back_orders: int = 1_000_000
    bulk_batches: int = 200
    rows_per_batch: int = 10_000


def run_one_day(table: OrdersTable, workload: DailyWorkload) -> None:
    """Apply one day of traffic; raises IdSpaceExhausted when inserts start failing."""
    table.insert_orders(workload.committed_orders, committed=True)
    table.insert_orders(workload.rolled_back_orders, committed=False)
    for _ in range(workload.bulk_batches):
        table.bulk_insert(workload.rows_per_batch)


def naive_should_alert(table: OrdersTable) -> bool:
    """Original check: compares the ROW COUNT with the id limit."""
    return table.row_count >= ROW_COUNT_ALERT_FRACTION * table.allocator.max_id


class HeadroomMonitor:
    """Forecasts days until the id counter hits the column's maximum."""

    def __init__(self, max_id: int = INT32_MAX, window_days: int = RATE_WINDOW_DAYS,
                 alert_lead_days: int = ALERT_LEAD_DAYS):
        self.max_id = max_id
        self.alert_lead_days = alert_lead_days
        # (day, last allocated id) pairs; one more than the window gives `window` deltas.
        self.readings: deque[tuple[int, int]] = deque(maxlen=window_days + 1)

    def record(self, day: int, last_allocated_id: int) -> None:
        """Store one daily reading of the id counter."""
        self.readings.append((day, last_allocated_id))

    def ids_per_day(self) -> float:
        """Average ids allocated per day over the readings in the window."""
        if len(self.readings) < 2:
            return 0.0
        first_day, first_id = self.readings[0]
        last_day, last_id = self.readings[-1]
        return (last_id - first_id) / (last_day - first_day)

    def days_until_exhaustion(self) -> float | None:
        """Days left at the recent rate; None when nothing is being allocated."""
        remaining = self.max_id - self.readings[-1][1]
        if remaining <= 0:
            return 0.0
        rate = self.ids_per_day()
        if rate <= 0:
            return None
        return remaining / rate

    def should_alert(self) -> bool:
        """Alert once the forecast falls inside the migration lead time."""
        days_left = self.days_until_exhaustion()
        return days_left is not None and days_left <= self.alert_lead_days


def migration_problems(column_types: dict[str, str], primary_key: str,
                       referencing_columns: list[str]) -> list[str]:
    """List columns that would still overflow after the key passes 2^31 - 1.

    Foreign keys matter as much as the key: a BIGINT orders.id is useless if
    order_items.order_id is still INT.
    """
    problems = []
    for column in [primary_key, *referencing_columns]:
        column_type = column_types[column].lower()
        if column_type not in SIXTY_FOUR_BIT_TYPES:
            problems.append(f"{column} is {column_type}; migrate it to bigint")
    return problems


@dataclass
class SimulationResult:
    exhaustion_day: int
    naive_alert_day: int | None
    headroom_alert_day: int | None
    row_fraction_at_exhaustion: float


def simulate(start_next_id: int = 600_000_000, start_rows: int = 400_000_000,
             workload: DailyWorkload = DailyWorkload(), max_days: int = 1_000) -> SimulationResult:
    """Run daily traffic until inserts fail, noting when each monitor first alerts."""
    table = OrdersTable(IdAllocator(start_next_id), start_rows)
    monitor = HeadroomMonitor()
    naive_alert_day = None
    headroom_alert_day = None
    for day in range(max_days):
        monitor.record(day, table.allocator.last_allocated)
        if naive_alert_day is None and naive_should_alert(table):
            naive_alert_day = day
        if headroom_alert_day is None and monitor.should_alert():
            headroom_alert_day = day
        try:
            run_one_day(table, workload)
        except IdSpaceExhausted:
            fraction = table.row_count / table.allocator.max_id
            return SimulationResult(day, naive_alert_day, headroom_alert_day, fraction)
    raise RuntimeError("ids did not run out within max_days")
