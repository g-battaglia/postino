"""Tests for SES and Mailgun email backends.

Covers header enforcement, config validation, dependency checks, and
provider-specific error handling.
"""

from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock, patch

from django.test import TestCase, override_settings

from apps.core.email_backend import (
    EmailBackendError,
    MailgunBackend,
    SESBackend,
    get_backend,
)

VALID_HEADERS = {
    "List-Unsubscribe": "<https://example.com/unsubscribe/?token=abc>",
    "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
}


# ---------------------------------------------------------------------------
# SES backend
# ---------------------------------------------------------------------------


class TestSESBackend(TestCase):
    def test_get_backend_returns_ses(self):
        with override_settings(POSTINO_EMAIL_PROVIDER="ses"):
            backend = get_backend()
            assert isinstance(backend, SESBackend)

    @override_settings(POSTINO_SES_AWS_ACCESS_KEY_ID="", POSTINO_SES_AWS_SECRET_ACCESS_KEY="")
    def test_validate_config_raises_when_missing(self):
        backend = SESBackend()
        with self.assertRaises(EmailBackendError) as ctx:
            backend._validate_config()
        assert "SES credentials" in str(ctx.exception)

    def test_send_raises_without_headers(self):
        backend = SESBackend()
        with self.assertRaises(EmailBackendError):
            backend.send("to@test.com", "Subject", "<p>Hi</p>", "Hi", {})

    @override_settings(
        POSTINO_SES_AWS_ACCESS_KEY_ID="key",
        POSTINO_SES_AWS_SECRET_ACCESS_KEY="secret",
        POSTINO_SES_AWS_REGION="us-east-1",
        POSTINO_EMAIL_FROM_NAME="Test",
        POSTINO_EMAIL_FROM_EMAIL="test@example.com",
    )
    @patch("apps.core.email_backend.SESBackend.send", side_effect=EmailBackendError("mock"))
    def test_send_validates_headers_before_calling_provider(self, mock_send):
        backend = SESBackend()
        with self.assertRaises(EmailBackendError):
            backend.send("to@test.com", "Subject", "<p>Hi</p>", "Hi", {})

    @override_settings(
        POSTINO_SES_AWS_ACCESS_KEY_ID="key",
        POSTINO_SES_AWS_SECRET_ACCESS_KEY="secret",
        POSTINO_SES_AWS_REGION="us-east-1",
        POSTINO_EMAIL_FROM_NAME="Test",
        POSTINO_EMAIL_FROM_EMAIL="test@example.com",
        POSTINO_EMAIL_REPLY_TO="reply@example.com",
    )
    def test_send_with_valid_headers_uses_raw_email(self):
        backend = SESBackend()
        sent_params = {}

        class FakeSESClient:
            def send_raw_email(self, **kwargs):
                sent_params.update(kwargs)
                return {"MessageId": "ses_msg_123"}

        fake_boto3 = types.SimpleNamespace(client=lambda *args, **kwargs: FakeSESClient())
        fake_botocore = types.ModuleType("botocore")
        fake_exceptions = types.ModuleType("botocore.exceptions")
        fake_exceptions.BotoCoreError = Exception
        fake_exceptions.ClientError = Exception

        with patch.dict(
            sys.modules,
            {
                "boto3": fake_boto3,
                "botocore": fake_botocore,
                "botocore.exceptions": fake_exceptions,
            },
        ):
            result = backend.send("to@test.com", "Subject", "<p>Hi</p>", "Hi", VALID_HEADERS)

        assert result == "ses_msg_123"
        assert sent_params["Source"] == "Test <test@example.com>"
        assert sent_params["Destinations"] == ["to@test.com"]
        raw = sent_params["RawMessage"]["Data"]
        assert b"List-Unsubscribe:" in raw
        assert b"List-Unsubscribe-Post:" in raw
        assert b"Reply-To: reply@example.com" in raw


# ---------------------------------------------------------------------------
# Mailgun backend
# ---------------------------------------------------------------------------


class TestMailgunBackend(TestCase):
    def test_get_backend_returns_mailgun(self):
        with override_settings(POSTINO_EMAIL_PROVIDER="mailgun"):
            backend = get_backend()
            assert isinstance(backend, MailgunBackend)

    @override_settings(POSTINO_MAILGUN_API_KEY="", POSTINO_MAILGUN_DOMAIN="")
    def test_validate_config_raises_when_missing(self):
        backend = MailgunBackend()
        with self.assertRaises(EmailBackendError) as ctx:
            backend._validate_config()
        assert "Mailgun" in str(ctx.exception)

    def test_send_raises_without_headers(self):
        backend = MailgunBackend()
        with self.assertRaises(EmailBackendError):
            backend.send("to@test.com", "Subject", "<p>Hi</p>", "Hi", {})

    @override_settings(
        POSTINO_MAILGUN_API_KEY="key",
        POSTINO_MAILGUN_DOMAIN="test.example.com",
        POSTINO_EMAIL_FROM_NAME="Test",
        POSTINO_EMAIL_FROM_EMAIL="test@example.com",
        POSTINO_EMAIL_REPLY_TO="",
    )
    @patch("urllib.request.urlopen")
    def test_send_with_valid_headers_calls_api(self, mock_urlopen):
        backend = MailgunBackend()
        mock_response = MagicMock()
        mock_response.read.return_value = b'{"id": "<msg_id@test>"}'
        mock_response.__enter__ = lambda s: mock_response
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_response

        result = backend.send(
            "to@test.com", "Subject", "<p>Hi</p>", "Hi", VALID_HEADERS
        )
        assert result == "msg_id@test"

    @override_settings(
        POSTINO_MAILGUN_API_KEY="key",
        POSTINO_MAILGUN_DOMAIN="test.example.com",
        POSTINO_EMAIL_FROM_NAME="Test",
        POSTINO_EMAIL_FROM_EMAIL="test@example.com",
    )
    @patch("urllib.request.urlopen")
    def test_send_handles_http_error(self, mock_urlopen):
        import urllib.error

        backend = MailgunBackend()
        error_response = MagicMock()
        error_response.read.return_value = b"error message"
        error_response.code = 400
        error_response.reason = "Bad Request"
        mock_urlopen.side_effect = urllib.error.HTTPError(
            "http://test", 400, "Bad Request", {}, error_response
        )
        with self.assertRaises(EmailBackendError) as ctx:
            backend.send(
                "to@test.com", "Subject", "<p>Hi</p>", "Hi", VALID_HEADERS
            )
        assert "400" in str(ctx.exception)


# ---------------------------------------------------------------------------
# Factory tests
# ---------------------------------------------------------------------------


class TestBackendFactory:
    def test_all_providers_in_backend_map(self):
        from apps.core.email_backend import _BACKEND_MAP

        assert "ses" in _BACKEND_MAP
        assert "mailgun" in _BACKEND_MAP
        assert "console" in _BACKEND_MAP
        assert "resend" in _BACKEND_MAP
        assert "smtp" in _BACKEND_MAP

    def test_unsupported_providers_dict_is_empty(self):
        from apps.core.email_backend import _UNSUPPORTED_PROVIDERS

        assert len(_UNSUPPORTED_PROVIDERS) == 0
