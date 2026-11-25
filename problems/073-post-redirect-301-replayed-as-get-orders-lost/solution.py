"""Canonical-URL redirect that does not turn order POSTs into empty GETs.

The API redirected every non-canonical path (``/v1/orders`` without the
trailing slash) to the canonical one with ``301 Moved Permanently``. Common
HTTP clients follow a 301 or 302 for a POST by sending a GET with no body, so
the order was never created; the GET hit the order *listing* and returned 200,
and the mobile app showed "order placed".

The fix only uses 301 for GET and HEAD. For every other method it answers
``308 Permanent Redirect``, which clients must replay with the same method and
body. ``client_send`` follows redirects the way common clients do (RFC 9110
section 15.4) so the tests show the difference end to end.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

STATUS_OK = 200
STATUS_CREATED = 201
STATUS_MOVED_PERMANENTLY = 301
STATUS_FOUND = 302
STATUS_SEE_OTHER = 303
STATUS_TEMPORARY_REDIRECT = 307
STATUS_PERMANENT_REDIRECT = 308
STATUS_NOT_FOUND = 404
STATUS_METHOD_NOT_ALLOWED = 405

# Methods that are safe to rewrite as GET: they carry no body that matters.
SAFE_METHODS = frozenset({"GET", "HEAD"})

# Clients stop after this many hops to avoid redirect loops.
MAX_REDIRECTS = 5

CANONICAL_ORDERS_PATH = "/v1/orders/"


@dataclass
class Request:
    """A minimal HTTP request."""
    method: str
    path: str
    body: bytes = b""


@dataclass
class Response:
    """A minimal HTTP response."""
    status: int
    location: str | None = None
    body: str = ""


@dataclass
class OrderStore:
    """In-memory order table so tests can see whether an order was created."""
    orders: list[bytes] = field(default_factory=list)


def handle_canonical(request: Request, store: OrderStore) -> Response:
    """Serve requests that already use the canonical path."""
    if request.method == "GET":
        return Response(STATUS_OK, body=f"{len(store.orders)} orders")
    if request.method == "POST":
        store.orders.append(request.body)
        return Response(STATUS_CREATED, body=f"order {len(store.orders)}")
    return Response(STATUS_METHOD_NOT_ALLOWED)


def canonical_path(path: str) -> str | None:
    """Return the canonical form of a known path, or None for unknown paths."""
    if path.rstrip("/") + "/" == CANONICAL_ORDERS_PATH:
        return CANONICAL_ORDERS_PATH
    return None


def naive_redirect_status(method: str) -> int:
    """Original behaviour: one permanent redirect code for every method."""
    return STATUS_MOVED_PERMANENTLY


def redirect_status(method: str) -> int:
    """Pick a redirect code that keeps the method and body for unsafe requests.

    301 is kept for GET/HEAD because caches and search engines understand it.
    Anything with a body gets 308, which forbids clients from switching to GET.
    """
    if method.upper() in SAFE_METHODS:
        return STATUS_MOVED_PERMANENTLY
    return STATUS_PERMANENT_REDIRECT


def make_server(store: OrderStore,
                choose_status: Callable[[str], int] = redirect_status) -> Callable[[Request], Response]:
    """Build a request handler; ``choose_status`` picks the redirect code."""
    def serve(request: Request) -> Response:
        target = canonical_path(request.path)
        if target is None:
            return Response(STATUS_NOT_FOUND)
        if target != request.path:
            return Response(choose_status(request.method), location=target)
        return handle_canonical(request, store)
    return serve


def follow_up_request(request: Request, response: Response) -> Request:
    """Build the request a typical client sends after a redirect.

    This mirrors browsers, ``requests`` and ``urllib``: a POST that gets 301 or
    302 is replayed as GET without a body, 303 always becomes GET (except
    HEAD), and 307/308 replay the exact method and body.
    """
    method = request.method
    body = request.body
    if response.status == STATUS_SEE_OTHER and method != "HEAD":
        method, body = "GET", b""
    elif response.status in (STATUS_MOVED_PERMANENTLY, STATUS_FOUND) and method == "POST":
        method, body = "GET", b""
    return Request(method, response.location or request.path, body)


def is_redirect(response: Response) -> bool:
    """True for the redirect codes a client follows automatically."""
    redirect_codes = (STATUS_MOVED_PERMANENTLY, STATUS_FOUND, STATUS_SEE_OTHER,
                      STATUS_TEMPORARY_REDIRECT, STATUS_PERMANENT_REDIRECT)
    return response.status in redirect_codes and response.location is not None


def client_send(serve: Callable[[Request], Response], request: Request) -> Response:
    """Send a request and follow redirects like a typical HTTP client."""
    response = serve(request)
    hops = 0
    while is_redirect(response) and hops < MAX_REDIRECTS:
        request = follow_up_request(request, response)
        response = serve(request)
        hops += 1
    return response
