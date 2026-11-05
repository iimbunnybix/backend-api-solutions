"""Compute invoice money without losing or inventing cents.

The original billing code used Python floats and ``round(x, 2)``:

* binary floats cannot represent most decimal amounts exactly, so
  ``round(2.675, 2)`` gives ``2.67`` (the stored value is 2.67499999...);
* rounding every line or installment independently means the parts no
  longer add up to the total (``round(100 / 3, 2) * 3 == 99.99``).

The fix keeps amounts as integer minor units (cents), uses ``Decimal`` with an
explicit rounding rule for tax, and *allocates* any remainder so parts always
sum exactly to the whole.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, ROUND_HALF_UP, Decimal
from typing import List, Sequence


# --------------------------------------------------------------------------
# Original float implementation (kept to reproduce the bug)
# --------------------------------------------------------------------------
def naive_tax(amount: float, rate: float) -> float:
    return round(amount * rate, 2)


def naive_installments(total: float, n: int) -> List[float]:
    return [round(total / n, 2)] * n


def naive_split_by_weight(total: float, weights: Sequence[float]) -> List[float]:
    s = sum(weights)
    return [round(total * w / s, 2) for w in weights]


# --------------------------------------------------------------------------
# Fixed implementation
# --------------------------------------------------------------------------
def to_cents(amount: str | Decimal) -> int:
    """Parse a decimal amount ("19.99") into integer cents.

    Floats are rejected on purpose: by the time a value is a float the
    rounding error has already happened.
    """
    if isinstance(amount, float):
        raise TypeError("pass money as a decimal string or Decimal, not float")
    d = Decimal(amount)
    cents = d * 100
    if cents != cents.to_integral_value():
        raise ValueError(f"{amount!r} has more than 2 decimal places")
    return int(cents)


def format_cents(cents: int) -> str:
    sign = "-" if cents < 0 else ""
    return f"{sign}{abs(cents) // 100}.{abs(cents) % 100:02d}"


def tax_cents(net_cents: int, rate: str | Decimal,
              rounding: str = ROUND_HALF_UP) -> int:
    """Tax on ``net_cents`` at ``rate`` (e.g. "0.0825"), rounded to a cent.

    The rounding mode is a business/legal decision: many tax authorities
    require half-up; ``ROUND_HALF_EVEN`` (banker's rounding) avoids upward bias
    when summing many rounded values.

    A float rate is rejected for the same reason ``to_cents`` rejects float
    money: ``Decimal(0.18)`` is 0.17999..., which brings the rounding bug back.
    """
    if isinstance(rate, float):
        raise TypeError("pass the tax rate as a decimal string or Decimal, not float")
    exact = Decimal(net_cents) * Decimal(rate)
    return int(exact.quantize(Decimal(1), rounding=rounding))


def allocate(total_cents: int, weights: Sequence[int | Decimal]) -> List[int]:
    """Split ``total_cents`` in proportion to ``weights``; parts sum exactly.

    Uses the largest-remainder method: give each part the floor of its exact
    share, then hand out the leftover cents one at a time to the parts with the
    largest fractional remainders (ties go to the earliest part, so the result
    is deterministic).
    """
    if not weights:
        raise ValueError("need at least one weight")
    ws = [Decimal(w) for w in weights]
    if any(w < 0 for w in ws) or sum(ws) == 0:
        raise ValueError("weights must be non-negative and not all zero")
    sign = -1 if total_cents < 0 else 1
    total = abs(total_cents)
    s = sum(ws)
    exact = [Decimal(total) * w / s for w in ws]
    parts = [int(x) for x in exact]                      # floor for non-negative
    leftover = total - sum(parts)
    order = sorted(range(len(ws)), key=lambda i: (-(exact[i] - parts[i]), i))
    for i in order[:leftover]:
        parts[i] += 1
    return [sign * p for p in parts]


def installments(total_cents: int, n: int) -> List[int]:
    """Equal installments whose sum is exactly ``total_cents``.

    The extra cents go to the first installments (33.34, 33.33, 33.33).
    """
    if n <= 0:
        raise ValueError("n must be positive")
    return allocate(total_cents, [1] * n)


@dataclass(frozen=True)
class Line:
    description: str
    unit_price: str        # decimal string, e.g. "4.99"
    quantity: int


@dataclass(frozen=True)
class Invoice:
    lines: List[int]       # net cents per line
    subtotal: int
    tax: int
    total: int


def build_invoice(lines: Sequence[Line], tax_rate: str,
                  rounding: str = ROUND_HALF_UP) -> Invoice:
    """Invoice with tax computed once on the subtotal (one rounding step).

    Computing tax per line and summing rounded values is a different (also
    legal in some places) policy and can differ by a cent; pick one policy and
    use it everywhere, including in refunds.
    """
    nets = [to_cents(l.unit_price) * l.quantity for l in lines]
    subtotal = sum(nets)
    tax = tax_cents(subtotal, tax_rate, rounding)
    return Invoice(nets, subtotal, tax, subtotal + tax)


__all__ = ["ROUND_HALF_EVEN", "ROUND_HALF_UP", "Invoice", "Line", "allocate",
           "build_invoice", "format_cents", "installments", "naive_installments",
           "naive_split_by_weight", "naive_tax", "tax_cents", "to_cents"]
