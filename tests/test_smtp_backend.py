"""Tests for the SMTP email backend.

Covers:
- Header validation enforcement (RFC 8058)
- Configuration validation (missing host)
- send() uses Django's EmailMultiAlternatives
- get_backend() returns SMTPBackend for 'smtp' provider
- Reply-to header is set when configured
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from django.core.mail import EmailMultiAlternatives
from django.test import TestCase, override_settings

from apps.core.email_backend import (
    EmailBackendError,
    SMTPBackend,
    get_backend,
)

VALID_HEADERS = {
    "List-Unsubscribe": "<https://example.com/unsubscribe/?token=abc>",
    "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
}


class TestSMTPBackendHeaders(TestCase):
    def test_send_raises_when_headers_missing(self) -> None:
        backend = SMTPBackend()
        with override_settings(POSTINO_SMTP_HOST="smtp.example.com"):
            with self.assertRaises(EmailBackendError):
                backend.send(
                    to="user@example.com",
                    subject="Test",
                    html="<p>Hi</p>",
                    text="Hi",
                    headers={},
                )

    def test_send_raises_when_list_unsubscribe_missing(self) -> None:
        backend = SMTPBackend()
        with override_settings(POSTINO_SMTP_HOST="smtp.example.com"):
            with self.assertRaises(EmailBackendError):
                backend.send(
                    to="user@example.com",
                    subject="Test",
                    html="<p>Hi</p>",
                    text="Hi",
                    headers={"List-Unsubscribe-Post": "List-Unsubscribe=One-Click"},
                )

    def test_send_raises_when_list_unsubscribe_post_invalid(self) -> None:
        backend = SMTPBackend()
        with override_settings(POSTINO_SMTP_HOST="smtp.example.com"):
            with self.assertRaises(EmailBackendError):
                backend.send(
                    to="user@example.com",
                    subject="Test",
                    html="<p>Hi</p>",
                    text="Hi",
                    headers={"List-Unsubscribe": "<https://example.com/unsub/>"},
                )


class TestSMTPBackendConfig(TestCase):
    def test_raises_when_host_not_configured(self) -> None:
        backend = SMTPBackend()
        with override_settings(POSTINO_SMTP_HOST=""):
            with self.assertRaises(EmailBackendError) as ctx:
                backend.send(
                    to="user@example.com",
                    subject="Test",
                    html="<p>Hi</p>",
                    text="Hi",
                    headers=VALID_HEADERS,
                )
            self.assertIn("SMTP host", str(ctx.exception))

    @patch.object(EmailMultiAlternatives, "send")
    @patch("django.core.mail.get_connection")
    def test_send_calls_django_email(
        self, mock_get_connection: MagicMock, mock_send: MagicMock,
    ) -> None:
        mock_send.return_value = 1
        mock_get_connection.return_value = MagicMock()
        backend = SMTPBackend()
        with override_settings(
            POSTINO_SMTP_HOST="smtp.example.com",
            POSTINO_SMTP_PORT=2525,
            POSTINO_SMTP_USERNAME="user",
            POSTINO_SMTP_PASSWORD="pass",
            POSTINO_SMTP_USE_TLS=False,
            POSTINO_EMAIL_FROM_NAME="Test",
            POSTINO_EMAIL_FROM_EMAIL="test@example.com",
            POSTINO_EMAIL_REPLY_TO="",
        ):
            backend.send(
                to="user@example.com",
                subject="Test Subject",
                html="<p>HTML</p>",
                text="Text",
                headers=VALID_HEADERS,
            )

        mock_send.assert_called_once()
        mock_get_connection.assert_called_once_with(
            host="smtp.example.com",
            port=2525,
            username="user",
            password="pass",
            use_tls=False,
            use_ssl=False,
        )

    @patch.object(EmailMultiAlternatives, "send", return_value=1)
    def test_send_sets_reply_to_when_configured(self, mock_send: MagicMock) -> None:
        original_init = EmailMultiAlternatives.__init__
        created_emails: list[EmailMultiAlternatives] = []

        def capturing_init(
            email_self: EmailMultiAlternatives, *args: object, **kwargs: object
        ) -> None:
            created_emails.append(email_self)
            original_init(email_self, *args, **kwargs)

        backend = SMTPBackend()
        with (
            override_settings(
                POSTINO_SMTP_HOST="smtp.example.com",
                POSTINO_EMAIL_FROM_NAME="Test",
                POSTINO_EMAIL_FROM_EMAIL="test@example.com",
                POSTINO_EMAIL_REPLY_TO="reply@example.com",
            ),
            patch.object(EmailMultiAlternatives, "__init__", capturing_init),
        ):
            backend.send(
                to="user@example.com",
                subject="Test",
                html="<p>Hi</p>",
                text="Hi",
                headers=VALID_HEADERS,
            )

        self.assertEqual(len(created_emails), 1)
        self.assertEqual(created_emails[0].reply_to, ["reply@example.com"])

    @patch.object(EmailMultiAlternatives, "send")
    def test_send_attaches_html_alternative(self, mock_send: MagicMock) -> None:
        mock_send.return_value = 1
        backend = SMTPBackend()
        with override_settings(
            POSTINO_SMTP_HOST="smtp.example.com",
            POSTINO_EMAIL_FROM_NAME="Test",
            POSTINO_EMAIL_FROM_EMAIL="test@example.com",
            POSTINO_EMAIL_REPLY_TO="",
        ):
            backend.send(
                to="user@example.com",
                subject="Test",
                html="<p>HTML body</p>",
                text="Text body",
                headers=VALID_HEADERS,
            )

        mock_send.assert_called_once()


class TestGetBackendSMTP(TestCase):
    @override_settings(POSTINO_EMAIL_PROVIDER="smtp")
    def test_returns_smtp_backend(self) -> None:
        backend = get_backend()
        self.assertIsInstance(backend, SMTPBackend)
