"""Resend/Svix-style webhook signature verification.

Resend uses Svix for webhook signing. The signature is computed as:

    signed_content = "{svix_id}.{svix_timestamp}.{body}"
    signature = base64(hmac_sha256(base64_decoded_secret, signed_content))

The ``svix-signature`` header contains one or more space-separated
``v1,<base64-signature>`` entries (versioned signatures). We check at
least one signature matches. Both base64 and hex-encoded signatures
are accepted for compatibility.

The signing secret may be prefixed with ``whsec_`` (as Resend provides
it); the prefix is stripped and the remainder is base64-decoded to get
the raw key bytes.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import time


class SignatureVerificationError(Exception):
    """Raised when a webhook signature fails verification."""


def _decode_secret(secret: str) -> bytes:
    """Decode the webhook signing secret to raw bytes.

    Handles:
    - ``whsec_`` prefix (Resend convention): strip prefix, base64-decode.
    - Plain base64 string: base64-decode.
    - Raw string: encode as UTF-8 bytes.

    Falls back to UTF-8 encoding if base64 decoding fails regardless
    of whether the ``whsec_`` prefix is present.
    """
    raw = secret[len("whsec_"):] if secret.startswith("whsec_") else secret
    try:
        return base64.b64decode(raw)
    except Exception:
        return raw.encode("utf-8")


def _compute_signature_base64(key: bytes, message: str) -> str:
    """Compute the HMAC-SHA256 signature and return it as a base64 string."""
    digest = hmac.new(key, message.encode("utf-8"), hashlib.sha256).digest()
    return base64.b64encode(digest).decode("ascii")


def _compute_signature_hex(key: bytes, message: str) -> str:
    """Compute the HMAC-SHA256 signature and return it as a hex string."""
    return hmac.new(key, message.encode("utf-8"), hashlib.sha256).hexdigest()


def verify_signature(
    *,
    body: str,
    svix_id: str,
    svix_timestamp: str,
    svix_signature: str,
    secret: str,
    tolerance_seconds: int = 300,
) -> None:
    """Verify a Svix-style webhook signature.

    Accepts both base64-encoded (canonical Svix) and hex-encoded
    (legacy compatibility) signature formats.

    Raises :class:`SignatureVerificationError` if verification fails.
    """
    if not secret:
        raise SignatureVerificationError("No webhook signing secret configured.")

    if not svix_id or not svix_timestamp or not svix_signature:
        raise SignatureVerificationError("Missing required Svix headers.")

    timestamp_now = int(time.time())
    try:
        timestamp_header = int(svix_timestamp.split(".")[0])
    except (ValueError, IndexError) as exc:
        raise SignatureVerificationError(
            "Invalid timestamp in svix-timestamp header.",
        ) from exc

    if abs(timestamp_now - timestamp_header) > tolerance_seconds:
        raise SignatureVerificationError("Timestamp outside tolerance window.")

    key = _decode_secret(secret)
    signed_content = f"{svix_id}.{svix_timestamp}.{body}"
    expected_base64 = _compute_signature_base64(key, signed_content)
    expected_hex = _compute_signature_hex(key, signed_content)

    for versioned in svix_signature.split(" "):
        parts = versioned.split(",", 1)
        if len(parts) != 2:
            continue
        version, signature = parts
        if version == "v1" and (
            hmac.compare_digest(expected_base64, signature)
            or hmac.compare_digest(expected_hex, signature)
        ):
            return

    raise SignatureVerificationError("No matching signature found.")


def build_signature_headers(
    *,
    body: str,
    secret: str,
    svix_id: str | None = None,
    timestamp: int | None = None,
) -> dict[str, str]:
    """Build valid Svix signature headers for testing.

    Produces headers with canonical base64-encoded signatures that will
    pass :func:`verify_signature`.
    """
    import uuid

    if svix_id is None:
        svix_id = f"msg_{uuid.uuid4().hex}"
    if timestamp is None:
        timestamp = int(time.time())

    svix_timestamp = str(timestamp)
    key = _decode_secret(secret)
    signed_content = f"{svix_id}.{svix_timestamp}.{body}"
    sig = _compute_signature_base64(key, signed_content)

    return {
        "svix-id": svix_id,
        "svix-timestamp": svix_timestamp,
        "svix-signature": f"v1,{sig}",
    }
