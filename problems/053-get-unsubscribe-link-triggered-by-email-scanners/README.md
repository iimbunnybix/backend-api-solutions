# 053 · Users were unsubscribed from the newsletter before they had even opened the email

## Problem
A B2B newsletter service sent a weekly digest to about 200,000 business
addresses. Every email contained a link `GET /unsubscribe?token=...`, and the
handler unsubscribed the address as soon as the link was requested. After a
large customer moved its mail to a gateway with link scanning, the
unsubscribe rate for that customer's domain jumped overnight; users
complained that they "never clicked anything" and had stopped receiving
invoices-due reminders that shared the same list. Support had to
re-subscribe people by hand, and some unsubscribes could not be told apart
from real ones.

## Root cause
Mail security gateways and link-preview bots fetch every link in an incoming
email (often with `HEAD` and then `GET`) to check the target for malware.
That is allowed: HTTP defines `GET` and `HEAD` as *safe* methods (RFC 9110,
section 9.2.1), meaning a client may request them without asking the user
and expect no state change. The handler broke that contract by changing
state on `GET`, so any automated fetch counted as the user's decision.

## Solution
`unsubscribe_handler` treats `GET` and `HEAD` as read-only: `GET` returns a
confirmation page whose button POSTs back to the same link, and `HEAD`
returns nothing. Only a `POST` with a confirmation unsubscribes. Two
confirmations are accepted: the page's button (`confirm=yes`) and the RFC
8058 one-click body `List-Unsubscribe=One-Click`, which mail clients send
when the email carries the `List-Unsubscribe` and `List-Unsubscribe-Post`
headers (`unsubscribe_email_headers`). Scanners do not submit forms or send
that POST, so their fetches are harmless. A repeated POST is a no-op, and any
other method gets `405` with an `Allow` header.

## Trade-offs
- One extra click for users who open the link in a browser; mail clients
  that support RFC 8058 still give a true one-click experience.
- Some aggressive scanners do submit forms. If that shows up, also require a
  short-lived signed value embedded in the page, or record the user agent and
  let support undo unsubscribes.
- Any other "magic link" (confirm email, accept invite, approve request) has
  the same problem and the same fix; sign-in links that must work on GET
  should be single-use *and* show an interstitial before acting.
- The same rule protects against browser prefetching and crawlers that follow
  links on public pages.

## Run
```bash
python -m unittest -v test_solution.py
```
