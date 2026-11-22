# 068 · Support agents searched for customers with "+" in their email, got "user not found", and created duplicate accounts

## Problem
The internal support tool looks up customers with
`GET /users?email=<address>` on the accounts API. Many customers use
sub-addressing (`maria+shop@example.com`) to filter shop mail.

For those customers, every search answered **404 user not found**. Agents
assumed the customer had never registered and created a new account under
the same address with a different case or without the tag. Orders, loyalty
points and saved cards were then split across two accounts, and merging them
became a weekly manual job for the support leads. The accounts API looked
healthy: the requests succeeded, they just found nothing.

## Root cause
The tool built the URL by string formatting:
`f"/users?email={email}"` (`naive_lookup_url`). Query strings are decoded
with the `application/x-www-form-urlencoded` rules (`parse_qs`), in which
**"+" means a space**. The server therefore looked up
`maria shop@example.com`, which does not exist. An `&` in the local part
(`o'neil&co@example.com`, valid in email) was worse: it split the value into
a second parameter and the server searched for `o'neil`.

The server made the bug invisible: a malformed address that cannot be a real
email produced the same 404 as a well-formed address that simply had no
account (`naive_handle`), so nobody looked at the input.

## Solution
Fix both sides:

1. **Client: always encode query values** (`lookup_url`). `urlencode` with
   `quote_via=quote` turns `+` into `%2B`, `&` into `%26` and `'` into
   `%27`, so the server decodes exactly the original string:
   `/users?email=maria%2Bshop@example.com`. (`@` is left readable; it is
   allowed unencoded in a query.) The encoded URL also works with the old
   server, so the client fix can ship first.
2. **Server: validate before looking up** (`handle`). A missing or empty
   value, a value given twice, a value without exactly one `@`, or a value
   containing whitespace gets **400** with a message. The whitespace message
   says to encode `+` as `%2B`, so the next client with the same bug learns
   it from the first response instead of from a support escalation. Only a
   well-formed address with no account gets 404.

## Trade-offs
- Rejecting whitespace means the server never "repairs" `maria shop@...`
  back to `maria+shop@...`. Guessing would also turn a genuine typo into a
  lookup of someone else's account, which is worse than an error.
- The validation here is a sanity check, not full RFC 5322 parsing; quoted
  local parts with spaces (`"john doe"@example.com`) are rejected. Almost no
  real system accepts those at signup, so the lookup does not need to either.
- Lookup is case-insensitive on the whole address. Strictly, the local part
  may be case-sensitive, but treating it so is what creates duplicate
  accounts in practice; see problem 020 for normalising at signup.
- An email address in a URL ends up in access logs, proxies and browser
  history. A `POST /users/search` with the address in a JSON body avoids
  both the encoding trap and the logging of personal data.
- HTTP libraries (`requests`' `params=`, `httpx`) encode query values for
  you; the bug comes back whenever someone builds a URL with string
  formatting, so a lint rule against f-string URLs helps.

## Run
```bash
python -m unittest -v test_solution.py
```
