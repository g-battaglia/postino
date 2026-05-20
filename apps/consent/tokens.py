"""Deterministic HMAC-SHA256 tokens for unsubscribe, preference center, and
double opt-in confirmation links.

Token format::

    base64url(uuid_bytes) "." base64url(hmac_sha256(secret, message))

Unscoped (global unsubscribe / preferences)::

    message = uuid_bytes

Email-type scoped::

    message = uuid_bytes ":" email_type_slug

Double opt-in confirmation::

    message = "optin:" uuid_bytes

Properties:

- Deterministic: same UUID + same optional email type => same token.
- No expiration, no DB lookup needed to verify.
- No PII (email address) in the token payload.
- Uses ``hmac.compare_digest`` for constant-time signature comparison.
- Different message prefixes prevent cross-purpose token confusion.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import uuid

from django.conf import settings


class InvalidToken(Exception):
    """Raised when a token is malformed, has an invalid signature, or carries bad data."""


def _get_secret(*, secret: str | None = None) -> bytes:
    raw = secret if secret is not None else settings.POSTINO_UNSUBSCRIBE_SECRET
    return raw.encode("utf-8")


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(text: str) -> bytes:
    padding = 4 - len(text) % 4
    if padding != 4:
        text += "=" * padding
    return base64.urlsafe_b64decode(text)


def generate_unsubscribe_token(
    subscriber_id: uuid.UUID | str,
    email_type_slug: str | None = None,
    *,
    secret: str | None = None,
) -> str:
    """Return a deterministic HMAC-SHA256 unsubscribe token.

    Parameters
    ----------
    subscriber_id:
        The subscriber's UUID (primary key).  No email or other PII is encoded.
    email_type_slug:
        Optional slug of an ``EmailType``.  When provided, the token is scoped
        to that specific email type.
    secret:
        Keyword-only override for the HMAC secret (useful in tests).

    Returns
    -------
    str
        Token in the form ``<base64url-payload>.<base64url-signature>``.
    """
    subscriber_uuid = uuid.UUID(str(subscriber_id))
    payload_bytes = subscriber_uuid.bytes

    message = payload_bytes
    if email_type_slug is not None:
        message = payload_bytes + b":" + email_type_slug.encode("utf-8")

    secret_bytes = _get_secret(secret=secret)
    signature = hmac.new(secret_bytes, message, hashlib.sha256).digest()

    return f"{_b64url_encode(payload_bytes)}.{_b64url_encode(signature)}"


def verify_unsubscribe_token(
    token: str,
    email_type_slug: str | None = None,
    *,
    secret: str | None = None,
) -> uuid.UUID:
    """Verify an unsubscribe token and return the embedded subscriber UUID.

    Parameters
    ----------
    token:
        The token string to verify.
    email_type_slug:
        The expected email type slug.  Pass ``None`` for unscoped (global)
        tokens.
    secret:
        Keyword-only override for the HMAC secret (useful in tests).

    Returns
    -------
    uuid.UUID
        The subscriber UUID encoded in the token.

    Raises
    ------
    InvalidToken
        If the token is malformed, the signature does not match, or the
        payload cannot be decoded as a valid UUID.
    """
    parts = token.split(".")
    if len(parts) != 2:
        raise InvalidToken("Token must contain exactly one dot separator.")

    payload_b64, signature_b64 = parts

    try:
        payload_bytes = _b64url_decode(payload_b64)
    except Exception as exc:
        raise InvalidToken("Token payload is not valid base64.") from exc

    try:
        signature_bytes = _b64url_decode(signature_b64)
    except Exception as exc:
        raise InvalidToken("Token signature is not valid base64.") from exc

    if len(payload_bytes) != 16:
        raise InvalidToken("Token payload must be exactly 16 bytes (UUID).")

    try:
        subscriber_uuid = uuid.UUID(bytes=payload_bytes)
    except ValueError as exc:
        raise InvalidToken("Token payload is not a valid UUID.") from exc

    message = payload_bytes
    if email_type_slug is not None:
        message = payload_bytes + b":" + email_type_slug.encode("utf-8")

    secret_bytes = _get_secret(secret=secret)
    expected_signature = hmac.new(secret_bytes, message, hashlib.sha256).digest()

    if not hmac.compare_digest(signature_bytes, expected_signature):
        raise InvalidToken("Token signature verification failed.")

    return subscriber_uuid


# ---------------------------------------------------------------------------
# Double opt-in confirmation tokens
# ---------------------------------------------------------------------------


def generate_double_optin_token(
    subscriber_id: uuid.UUID | str,
    *,
    secret: str | None = None,
) -> str:
    """Return a deterministic HMAC-SHA256 double opt-in confirmation token.

    Uses a ``optin:`` prefix in the message so that this token cannot be
    confused with an unsubscribe token (and vice versa).

    Parameters
    ----------
    subscriber_id:
        The subscriber's UUID (primary key).
    secret:
        Keyword-only override for the HMAC secret (useful in tests).

    Returns
    -------
    str
        Token in the form ``<base64url-payload>.<base64url-signature>``.
    """
    subscriber_uuid = uuid.UUID(str(subscriber_id))
    payload_bytes = subscriber_uuid.bytes
    message = b"optin:" + payload_bytes

    secret_bytes = _get_secret(secret=secret)
    signature = hmac.new(secret_bytes, message, hashlib.sha256).digest()

    return f"{_b64url_encode(payload_bytes)}.{_b64url_encode(signature)}"


def verify_double_optin_token(
    token: str,
    *,
    secret: str | None = None,
) -> uuid.UUID:
    """Verify a double opt-in confirmation token and return the subscriber UUID.

    Parameters
    ----------
    token:
        The token string to verify.
    secret:
        Keyword-only override for the HMAC secret (useful in tests).

    Returns
    -------
    uuid.UUID
        The subscriber UUID encoded in the token.

    Raises
    ------
    InvalidToken
        If the token is malformed, the signature does not match, or the
        payload cannot be decoded as a valid UUID.
    """
    parts = token.split(".")
    if len(parts) != 2:
        raise InvalidToken("Token must contain exactly one dot separator.")

    payload_b64, signature_b64 = parts

    try:
        payload_bytes = _b64url_decode(payload_b64)
    except Exception as exc:
        raise InvalidToken("Token payload is not valid base64.") from exc

    try:
        signature_bytes = _b64url_decode(signature_b64)
    except Exception as exc:
        raise InvalidToken("Token signature is not valid base64.") from exc

    if len(payload_bytes) != 16:
        raise InvalidToken("Token payload must be exactly 16 bytes (UUID).")

    try:
        subscriber_uuid = uuid.UUID(bytes=payload_bytes)
    except ValueError as exc:
        raise InvalidToken("Token payload is not a valid UUID.") from exc

    message = b"optin:" + payload_bytes

    secret_bytes = _get_secret(secret=secret)
    expected_signature = hmac.new(secret_bytes, message, hashlib.sha256).digest()

    if not hmac.compare_digest(signature_bytes, expected_signature):
        raise InvalidToken("Token signature verification failed.")

    return subscriber_uuid
