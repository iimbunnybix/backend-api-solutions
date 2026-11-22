"""Look up a user by an email address that contains "+" in the query string.

The support tool called ``GET /users?email=`` by gluing the raw address into
the URL. In a query string, form decoding (``parse_qs``) turns "+" into a
space, so ``maria+shop@example.com`` arrived as ``maria shop@example.com``.
The lookup said "no such user" and agents created duplicate accounts.

The fix has two halves: the client percent-encodes every query value
(``urlencode`` with ``quote_via=quote``, so "+" becomes "%2B"), and the
server validates the decoded email and answers 400 with a clear message when
it contains a space, instead of a misleading 404.
"""
from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import parse_qs, quote, urlencode, urlsplit

USERS_PATH = "/users"
HTTP_OK = 200
HTTP_BAD_REQUEST = 400
HTTP_NOT_FOUND = 404
# Characters that never appear unquoted in an email we accept. A space here
# almost always means a "+" that was decoded by form decoding.
FORBIDDEN_EMAIL_CHARACTERS = (" ", "\t", "\r", "\n")


@dataclass(frozen=True)
class Response:
    status: int
    body: dict


class UserDirectory:
    """In-memory user store keyed by lower-cased email."""

    def __init__(self, emails: list[str]) -> None:
        self._ids: dict[str, int] = {}
        for user_id, email in enumerate(emails, start=1):
            self._ids[email.lower()] = user_id

    def find(self, email: str) -> int | None:
        return self._ids.get(email.lower())


# --- the original, buggy version ---------------------------------------------
def naive_lookup_url(email: str) -> str:
    """Original client: pastes the raw email into the URL."""
    return f"{USERS_PATH}?email={email}"


def naive_handle(url: str, directory: UserDirectory) -> Response:
    """Original server: any decoded value is looked up; failures look like 404."""
    query = parse_qs(urlsplit(url).query)
    email = query.get("email", [""])[0]
    user_id = directory.find(email)
    if user_id is None:
        return Response(HTTP_NOT_FOUND, {"error": "user not found"})
    return Response(HTTP_OK, {"id": user_id, "email": email})


# --- the fix -------------------------------------------------------------------
def lookup_url(email: str) -> str:
    """Client: percent-encode the value so "+", "&", "=" and "#" survive."""
    # The default quote_via=quote_plus would turn spaces into "+" but still
    # encode "+" as "%2B"; quote is used so the URL never contains "+" at all.
    query = urlencode({"email": email}, quote_via=quote, safe="@")
    return f"{USERS_PATH}?{query}"


def validation_error(email: str) -> str | None:
    """Explain why a decoded email is unusable, or return None if it is fine."""
    if not email:
        return "email query parameter is required"
    for character in FORBIDDEN_EMAIL_CHARACTERS:
        if character in email:
            return ("email contains whitespace; if it has a '+', "
                    "percent-encode it as %2B")
    if email.count("@") != 1:
        return "email must contain exactly one '@'"
    return None


def single_email_param(url: str) -> tuple[str | None, str | None]:
    """Return (email, error) for the request; repeated params are an error."""
    # keep_blank_values so "?email=" is reported as empty, not as missing.
    query = parse_qs(urlsplit(url).query, keep_blank_values=True)
    values = query.get("email", [])
    if len(values) > 1:
        return None, "email query parameter given more than once"
    email = values[0] if values else ""
    error = validation_error(email)
    if error is not None:
        return None, error
    return email, None


def handle(url: str, directory: UserDirectory) -> Response:
    """Server: reject malformed input with 400 so it is never mistaken for 'no such user'."""
    email, error = single_email_param(url)
    if error is not None:
        return Response(HTTP_BAD_REQUEST, {"error": error})
    user_id = directory.find(email)
    if user_id is None:
        return Response(HTTP_NOT_FOUND, {"error": "user not found"})
    return Response(HTTP_OK, {"id": user_id, "email": email})
