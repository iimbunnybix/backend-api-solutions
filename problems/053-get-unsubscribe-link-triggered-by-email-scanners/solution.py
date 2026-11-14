"""Unsubscribe links that email security scanners "clicked" for the user.

The newsletter service put a link like GET /unsubscribe?token=... in every
email, and the GET handler unsubscribed the user immediately. Corporate mail
gateways and link-preview bots fetch every link in incoming mail to check it
for malware, so users were unsubscribed before they even opened the email.
GET is defined as a "safe" method (RFC 9110): fetching it must not change
state. This version makes GET show a confirmation page only and performs the
unsubscribe on POST, including the RFC 8058 one-click POST that mail clients send.
"""
from __future__ import annotations

from dataclasses import dataclass, field

HTTP_OK = 200
HTTP_BAD_REQUEST = 400
HTTP_NOT_FOUND = 404
HTTP_METHOD_NOT_ALLOWED = 405
# Body that RFC 8058 one-click unsubscribe clients POST to the link.
ONE_CLICK_FORM_FIELD = "List-Unsubscribe"
ONE_CLICK_FORM_VALUE = "One-Click"
ALLOWED_METHODS = "GET, HEAD, POST"


@dataclass
class Request:
    method: str
    query: dict[str, str] = field(default_factory=dict)
    form: dict[str, str] = field(default_factory=dict)


@dataclass
class Response:
    status: int
    body: str = ""
    headers: dict[str, str] = field(default_factory=dict)


class MailingList:
    """Who is subscribed, and which unsubscribe token belongs to which address."""

    def __init__(self) -> None:
        self.subscribed: set[str] = set()
        self.token_owner: dict[str, str] = {}

    def add(self, email: str, token: str) -> None:
        """Subscribe `email`; `token` goes into the unsubscribe link of their emails."""
        self.subscribed.add(email)
        self.token_owner[token] = email

    def is_subscribed(self, email: str) -> bool:
        return email in self.subscribed


def naive_unsubscribe_handler(mailing_list: MailingList, request: Request) -> Response:
    """Original handler: any request to the link, including GET, unsubscribes."""
    email = mailing_list.token_owner.get(request.query.get("token", ""))
    if email is None:
        return Response(HTTP_NOT_FOUND, "unknown link")
    mailing_list.subscribed.discard(email)
    return Response(HTTP_OK, "You have been unsubscribed.")


def confirmation_page(token: str) -> str:
    """HTML with a button that POSTs back to the same link; showing it changes nothing."""
    return (
        "<p>Unsubscribe from the newsletter?</p>"
        f'<form method="post" action="/unsubscribe?token={token}">'
        '<button type="submit">Unsubscribe</button></form>'
    )


def is_confirmed_post(request: Request) -> bool:
    """True for the button on our page or an RFC 8058 one-click POST from a mail client."""
    if request.form.get("confirm") == "yes":
        return True
    return request.form.get(ONE_CLICK_FORM_FIELD) == ONE_CLICK_FORM_VALUE


def unsubscribe_handler(mailing_list: MailingList, request: Request) -> Response:
    """Fixed handler: GET/HEAD are read-only, only a confirmed POST unsubscribes."""
    token = request.query.get("token", "")
    email = mailing_list.token_owner.get(token)
    if email is None:
        return Response(HTTP_NOT_FOUND, "unknown link")

    if request.method in ("GET", "HEAD"):
        # Scanners and previewers stop here, so the subscription is untouched.
        body = "" if request.method == "HEAD" else confirmation_page(token)
        return Response(HTTP_OK, body)

    if request.method == "POST":
        if not is_confirmed_post(request):
            return Response(HTTP_BAD_REQUEST, "missing confirmation")
        # discard() makes a repeated POST (double click, client retry) harmless.
        mailing_list.subscribed.discard(email)
        return Response(HTTP_OK, "You have been unsubscribed.")

    return Response(HTTP_METHOD_NOT_ALLOWED, "", {"Allow": ALLOWED_METHODS})


def unsubscribe_email_headers(link: str) -> dict[str, str]:
    """Headers that let mail clients offer one-click unsubscribe via POST (RFC 8058)."""
    return {
        "List-Unsubscribe": f"<{link}>",
        "List-Unsubscribe-Post": f"{ONE_CLICK_FORM_FIELD}={ONE_CLICK_FORM_VALUE}",
    }
