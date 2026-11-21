"""Opaque case tokens.

A token is `a15g.<grant id>.<signature>`: a random grant ID and an HMAC over it. It carries no personal data;
the identifiers it unlocks live in the scope store. A forged or altered token fails the signature check before
any database is touched.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

from article15.settings import MIN_SECRET_BYTES

PREFIX = "a15g"


class InvalidToken(Exception):
    pass


def _signature(secret: bytes, grant_id: str) -> str:
    return hmac.new(secret, f"grant|{grant_id}".encode(), hashlib.sha256).hexdigest()


def new_grant_id() -> str:
    return secrets.token_hex(16)


def issue(secret: bytes, grant_id: str) -> str:
    if len(secret) < MIN_SECRET_BYTES:
        raise ValueError(f"scope secret must be at least {MIN_SECRET_BYTES} bytes")
    return f"{PREFIX}.{grant_id}.{_signature(secret, grant_id)}"


def grant_id_of(secret: bytes, token: str) -> str:
    parts = token.strip().split(".")
    if len(parts) != 3 or parts[0] != PREFIX or not parts[1] or not parts[2]:
        raise InvalidToken("malformed case token")
    if not hmac.compare_digest(_signature(secret, parts[1]), parts[2]):
        raise InvalidToken("case token signature does not match")
    return parts[1]
