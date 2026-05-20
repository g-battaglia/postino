"""Tests for HMAC unsubscribe/preference tokens.

Target: 100 % coverage of ``apps.consent.tokens``.
All tests are pure unit tests -- no database required.
"""

from __future__ import annotations

import re
import uuid

import pytest

from apps.consent.tokens import (
    InvalidToken,
    generate_unsubscribe_token,
    verify_unsubscribe_token,
)

# --- Shared constants -------------------------------------------------------

_TEST_SECRET = "test-secret-that-is-at-least-32-characters-long"
_UUID = uuid.UUID("12345678-1234-5678-1234-567812345678")
_OTHER_UUID = uuid.UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
_EMAIL_TYPE = "weekly_digest"
_OTHER_EMAIL_TYPE = "product_update"

# URL-safe base64 alphabet: [A-Za-z0-9_-]
_B64URL_RE = re.compile(r"^[A-Za-z0-9_-]+$")


# --- Helpers ----------------------------------------------------------------


def _make_token(subscriber_id=_UUID, slug=None, secret=_TEST_SECRET):
    return generate_unsubscribe_token(subscriber_id, slug, secret=secret)


def _verify(token, slug=None, secret=_TEST_SECRET):
    return verify_unsubscribe_token(token, slug, secret=secret)


# --- Generation tests -------------------------------------------------------


class TestGenerateToken:
    def test_returns_string_with_one_dot(self):
        token = _make_token()
        assert isinstance(token, str)
        assert token.count(".") == 1

    def test_payload_is_url_safe_base64(self):
        payload, _signature = _make_token().split(".")
        assert _B64URL_RE.match(payload)

    def test_signature_is_url_safe_base64(self):
        _payload, signature = _make_token().split(".")
        assert _B64URL_RE.match(signature)

    def test_deterministic_same_uuid_same_secret(self):
        assert _make_token() == _make_token()

    def test_deterministic_accepts_uuid_string(self):
        token_from_uuid = _make_token(subscriber_id=_UUID)
        token_from_str = _make_token(subscriber_id=str(_UUID))
        assert token_from_uuid == token_from_str

    def test_different_uuids_produce_different_tokens(self):
        assert _make_token(subscriber_id=_UUID) != _make_token(subscriber_id=_OTHER_UUID)

    def test_scoped_token_differs_from_unscoped(self):
        unscoped = _make_token(slug=None)
        scoped = _make_token(slug=_EMAIL_TYPE)
        assert unscoped != scoped

    def test_different_scopes_produce_different_tokens(self):
        t1 = _make_token(slug=_EMAIL_TYPE)
        t2 = _make_token(slug=_OTHER_EMAIL_TYPE)
        assert t1 != t2

    def test_payload_does_not_contain_email_address(self):
        token = _make_token()
        assert "user@example.com" not in token
        assert "@" not in token.split(".")[0]


# --- Verification tests: valid tokens ---------------------------------------


class TestVerifyValidToken:
    def test_unscoped_returns_subscriber_uuid(self):
        token = _make_token()
        result = _verify(token)
        assert result == _UUID

    def test_scoped_returns_subscriber_uuid_with_correct_slug(self):
        token = _make_token(slug=_EMAIL_TYPE)
        result = _verify(token, slug=_EMAIL_TYPE)
        assert result == _UUID

    def test_uuid_type_returned(self):
        token = _make_token()
        result = _verify(token)
        assert isinstance(result, uuid.UUID)


# --- Verification tests: failures -------------------------------------------


class TestVerifyFailures:
    def test_scoped_token_fails_with_wrong_slug(self):
        token = _make_token(slug=_EMAIL_TYPE)
        with pytest.raises(InvalidToken, match="signature verification failed"):
            _verify(token, slug=_OTHER_EMAIL_TYPE)

    def test_scoped_token_fails_without_slug(self):
        token = _make_token(slug=_EMAIL_TYPE)
        with pytest.raises(InvalidToken, match="signature verification failed"):
            _verify(token, slug=None)

    def test_unscoped_token_fails_when_verified_as_scoped(self):
        token = _make_token(slug=None)
        with pytest.raises(InvalidToken, match="signature verification failed"):
            _verify(token, slug=_EMAIL_TYPE)

    def test_tampered_payload_fails(self):
        token = _make_token()
        payload, signature = token.split(".")
        tampered = "AAAA" + payload[4:] + "." + signature
        with pytest.raises(InvalidToken):
            _verify(tampered)

    def test_tampered_signature_fails(self):
        token = _make_token()
        payload, signature = token.split(".")
        tampered = payload + "." + "AAAA" + signature[4:]
        with pytest.raises(InvalidToken, match="signature verification failed"):
            _verify(tampered)

    def test_missing_dot_fails(self):
        with pytest.raises(InvalidToken, match="exactly one dot"):
            _verify("nodothere")

    def test_too_many_dots_fails(self):
        with pytest.raises(InvalidToken, match="exactly one dot"):
            _verify("a.b.c")

    def test_invalid_base64_payload_fails(self):
        with pytest.raises(InvalidToken, match="base64"):
            _verify("!!!invalid!!.validsig")

    def test_invalid_base64_signature_fails(self):
        payload, _ = _make_token().split(".")
        with pytest.raises(InvalidToken, match="base64"):
            _verify(payload + ".!!!invalid!!")

    def test_short_payload_fails(self):
        import base64

        short = base64.urlsafe_b64encode(b"short").rstrip(b"=").decode()
        _, sig = _make_token().split(".")
        with pytest.raises(InvalidToken, match="16 bytes"):
            _verify(f"{short}.{sig}")

    def test_wrong_secret_fails(self):
        token = _make_token(secret=_TEST_SECRET)
        with pytest.raises(InvalidToken, match="signature verification failed"):
            _verify(token, secret="different-secret-at-least-32-characters!!")


# --- Settings integration ---------------------------------------------------


class TestSettingsIntegration:
    def test_postino_unsubscribe_secret_exists(self):
        from django.conf import settings

        assert hasattr(settings, "POSTINO_UNSUBSCRIBE_SECRET")
        assert len(settings.POSTINO_UNSUBSCRIBE_SECRET) >= 32
