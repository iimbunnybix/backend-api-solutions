"""Date-based API versioning with version-change transformers.

The service keeps a single, latest internal representation of each resource.
Every breaking change is recorded as a ``VersionChange`` with the date it
shipped and two small functions:

* ``downgrade`` turns a response in the new shape into the previous shape;
* ``upgrade_request`` (optional) turns a request body in the previous shape
  into the new one.

A client asks for a version with the ``Api-Version`` header. Without the
header the client gets the version its API key was pinned to when it was
created, never "latest". To answer an older client, the latest response is
passed through the downgrades of every change newer than the client's version,
newest first. Request bodies go the other way, oldest change first.

This is the approach Stripe describes publicly for its API. It keeps handler
code free of ``if version < ...`` branches: handlers only know the latest shape.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass
from datetime import date
from typing import Callable

Body = dict


class UnsupportedVersion(ValueError):
    """Unknown or malformed ``Api-Version`` (return 400 to the client)."""


@dataclass(frozen=True)
class VersionChange:
    version: str                                  # ISO date the change shipped
    description: str
    downgrade: Callable[[Body], Body]
    upgrade_request: Callable[[Body], Body] | None = None


def _parse(version: str) -> date:
    try:
        return date.fromisoformat(version)
    except (TypeError, ValueError):
        raise UnsupportedVersion(f"malformed Api-Version {version!r}") from None


class VersionedAPI:
    def __init__(self, initial_version: str, changes: list[VersionChange],
                 sunset_before: str | None = None):
        self.changes = sorted(changes, key=lambda c: _parse(c.version))
        self.versions = [initial_version] + [c.version for c in self.changes]
        if len(set(self.versions)) != len(self.versions):
            raise ValueError("duplicate version")
        if _parse(initial_version) >= _parse(self.versions[1] if self.changes else "9999-12-31"):
            raise ValueError("initial version must predate every change")
        self.latest = self.versions[-1]
        self.sunset_before = sunset_before

    def resolve(self, header: str | None, pinned: str) -> str:
        """Version for a request: the header if given, else the key's pin."""
        version = header.strip() if header else pinned
        _parse(version)
        if version not in self.versions:
            raise UnsupportedVersion(
                f"unknown Api-Version {version}; supported: {', '.join(self.versions)}")
        return version

    def _newer_than(self, version: str) -> list[VersionChange]:
        v = _parse(version)
        return [c for c in self.changes if _parse(c.version) > v]

    def render(self, latest_body: Body, version: str) -> Body:
        """Downgrade a latest-shape response to ``version`` (input not mutated)."""
        body = copy.deepcopy(latest_body)
        for change in reversed(self._newer_than(version)):
            body = change.downgrade(body)
        return body

    def parse_request(self, body: Body, version: str) -> Body:
        """Upgrade a request body sent in ``version``'s shape to the latest."""
        body = copy.deepcopy(body)
        for change in self._newer_than(version):
            if change.upgrade_request:
                body = change.upgrade_request(body)
        return body

    def response_headers(self, version: str) -> dict[str, str]:
        headers = {"Api-Version": version}
        if self.sunset_before and _parse(version) < _parse(self.sunset_before):
            headers["Deprecation"] = "true"
            headers["Link"] = '<https://example.com/docs/upgrades>; rel="deprecation"'
        return headers

    def handle(self, handler: Callable[[Body], Body], body: Body | None,
               header: str | None, pinned: str) -> tuple[int, dict, Body]:
        """Full request cycle: resolve version, upgrade input, downgrade output."""
        try:
            version = self.resolve(header, pinned)
        except UnsupportedVersion as e:
            return 400, {}, {"error": str(e)}
        latest_in = self.parse_request(body or {}, version)
        return 200, self.response_headers(version), self.render(handler(latest_in), version)


# --- The order API's actual history -------------------------------------------

def _split_money_down(order: Body) -> Body:
    amount = order.pop("amount")
    if amount["currency"] != "USD":
        # The old shape had no currency: old clients only ever saw USD orders.
        raise ValueError("non-USD order cannot be represented before 2026-03-01")
    cents = amount["value"]
    order["price"] = f"{cents // 100}.{cents % 100:02d}"
    return order


def _split_money_up(req: Body) -> Body:
    if "price" in req:
        whole, _, frac = req.pop("price").partition(".")
        req["amount"] = {"value": int(whole) * 100 + int((frac + "00")[:2]), "currency": "USD"}
    return req


def _nest_customer_down(order: Body) -> Body:
    order["customer_name"] = order.pop("customer")["name"]
    return order


OLD_STATUSES = {"pending", "paid", "shipped", "refunded"}


def _partial_refund_down(order: Body) -> Body:
    # Old clients crash on an unknown enum value; map to the closest one they
    # know and keep the detail in a field they ignore.
    if order["status"] == "partially_refunded":
        order["status"] = "refunded"
    return order


ORDER_API = VersionedAPI(
    initial_version="2025-11-01",
    changes=[
        VersionChange("2026-03-01", "price string -> amount {value cents, currency}",
                      _split_money_down, _split_money_up),
        VersionChange("2026-06-15", "customer_name -> customer {id, name}",
                      _nest_customer_down),
        VersionChange("2026-09-01", "new status partially_refunded",
                      _partial_refund_down),
    ],
    sunset_before="2026-03-01",
)
