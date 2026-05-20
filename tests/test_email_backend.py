"""Tests for apps.core.email_backend.

Covers:
- Unsubscribe header validation (pass, missing List-Unsubscribe, missing/invalid
  List-Unsubscribe-Post).
- ConsoleBackend.send outputs headers and returns None.
- ResendBackend.send builds correct payload and returns message ID via
  monkeypatched resend.Emails.send.
- ResendBackend does not call provider when headers are missing.
- get_backend() returns correct backend for console/resend.
- Unsupported/unknown providers raise EmailBackendError.
"""

from __future__ import annotations

import sys
from contextlib import contextmanager
from io import StringIO

from django.test import TestCase, override_settings

from apps.core.email_backend import (
    ConsoleBackend,
    EmailBackendError,
    ResendBackend,
    SMTPBackend,
    _validate_unsubscribe_headers,
    get_backend,
)

VALID_HEADERS = {
    "List-Unsubscribe": "<https://example.com/unsubscribe/?token=abc>",
    "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
}


@contextmanager
def capture_stdout():
    buf = StringIO()
    old_stdout = sys.stdout
    sys.stdout = buf
    try:
        yield buf
    finally:
        sys.stdout = old_stdout


# ---------------------------------------------------------------------------
# Header validation
# ---------------------------------------------------------------------------


class TestValidateUnsubscribeHeaders(TestCase):
    def test_passes_with_both_required_headers(self) -> None:
        _validate_unsubscribe_headers(VALID_HEADERS)

    def test_raises_when_list_unsubscribe_missing(self) -> None:
        headers = {"List-Unsubscribe-Post": "List-Unsubscribe=One-Click"}
        with self.assertRaises(EmailBackendError) as ctx:
            _validate_unsubscribe_headers(headers)
        self.assertIn("List-Unsubscribe", str(ctx.exception))

    def test_raises_when_list_unsubscribe_empty(self) -> None:
        headers = {
            "List-Unsubscribe": "  ",
            "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
        }
        with self.assertRaises(EmailBackendError) as ctx:
            _validate_unsubscribe_headers(headers)
        self.assertIn("List-Unsubscribe", str(ctx.exception))

    def test_raises_when_list_unsubscribe_post_missing(self) -> None:
        headers = {"List-Unsubscribe": "<https://example.com/unsubscribe/?token=abc>"}
        with self.assertRaises(EmailBackendError) as ctx:
            _validate_unsubscribe_headers(headers)
        self.assertIn("List-Unsubscribe-Post", str(ctx.exception))

    def test_raises_when_list_unsubscribe_post_has_wrong_value(self) -> None:
        headers = {
            "List-Unsubscribe": "<https://example.com/unsubscribe/?token=abc>",
            "List-Unsubscribe-Post": "SomeOtherValue",
        }
        with self.assertRaises(EmailBackendError) as ctx:
            _validate_unsubscribe_headers(headers)
        self.assertIn("List-Unsubscribe-Post", str(ctx.exception))

    def test_passes_when_post_header_has_extra_values(self) -> None:
        headers = {
            "List-Unsubscribe": "<https://example.com/unsubscribe/?token=abc>",
            "List-Unsubscribe-Post": "List-Unsubscribe=One-Click, Other=Value",
        }
        _validate_unsubscribe_headers(headers)


# ---------------------------------------------------------------------------
# ConsoleBackend
# ---------------------------------------------------------------------------


class TestConsoleBackend(TestCase):
    def test_send_returns_none(self) -> None:
        backend = ConsoleBackend()
        result = backend.send(
            to="user@example.com",
            subject="Test",
            html="<p>Hello</p>",
            text="Hello",
            headers=VALID_HEADERS,
        )
        self.assertIsNone(result)

    def test_send_includes_headers_in_output(self) -> None:
        backend = ConsoleBackend()
        with capture_stdout() as buf:
            backend.send(
                to="user@example.com",
                subject="Welcome",
                html="<p>Hi</p>",
                text="Hi",
                headers=VALID_HEADERS,
            )
        output = buf.getvalue()
        self.assertIn("List-Unsubscribe:", output)
        self.assertIn("List-Unsubscribe-Post:", output)

    def test_send_includes_subject_and_recipient(self) -> None:
        backend = ConsoleBackend()
        with capture_stdout() as buf:
            backend.send(
                to="alice@example.com",
                subject="Your weekly digest",
                html="<p>Content</p>",
                text="Content",
                headers=VALID_HEADERS,
            )
        output = buf.getvalue()
        self.assertIn("alice@example.com", output)
        self.assertIn("Your weekly digest", output)

    def test_send_raises_when_headers_missing(self) -> None:
        backend = ConsoleBackend()
        with self.assertRaises(EmailBackendError):
            backend.send(
                to="user@example.com",
                subject="Test",
                html="<p>Hi</p>",
                text="Hi",
                headers={},
            )


# ---------------------------------------------------------------------------
# ResendBackend
# ---------------------------------------------------------------------------


class TestResendBackend(TestCase):
    def setUp(self) -> None:
        import resend

        self._original_send = resend.Emails.send
        self._original_api_key = getattr(resend, "api_key", None)

    def tearDown(self) -> None:
        import resend

        resend.Emails.send = self._original_send
        resend.api_key = self._original_api_key

    def test_send_builds_payload_and_returns_id(self) -> None:
        backend = ResendBackend()
        sent_payload: dict = {}

        def fake_send(payload: dict) -> dict:
            sent_payload.update(payload)
            return {"id": "msg_abc123"}

        with override_settings(POSTINO_RESEND_API_KEY="re_test_key"):
            self._patch_resend_send(fake_send)
            result = backend.send(
                to="user@example.com",
                subject="Test subject",
                html="<p>Hello</p>",
                text="Hello",
                headers=VALID_HEADERS,
            )

        self.assertEqual(result, "msg_abc123")
        self._assert_common_payload(sent_payload, "user@example.com", "Test subject")

    def test_send_sets_api_key_before_provider_call(self) -> None:
        backend = ResendBackend()
        captured_key: str = ""

        def fake_send(payload: dict) -> dict:
            import resend

            nonlocal captured_key
            captured_key = resend.api_key
            return {"id": "msg_key_test"}

        with override_settings(POSTINO_RESEND_API_KEY="re_configured_key_123"):
            self._patch_resend_send(fake_send)
            backend.send(
                to="user@example.com",
                subject="Test",
                html="<p>Hi</p>",
                text="Hi",
                headers=VALID_HEADERS,
            )

        self.assertEqual(captured_key, "re_configured_key_123")

    def test_send_returns_none_when_no_id_in_response(self) -> None:
        backend = ResendBackend()

        def fake_send(payload: dict) -> dict:
            return {}

        with override_settings(POSTINO_RESEND_API_KEY="re_test_key"):
            self._patch_resend_send(fake_send)
            result = backend.send(
                to="user@example.com",
                subject="Test",
                html="<p>Hi</p>",
                text="Hi",
                headers=VALID_HEADERS,
            )

        self.assertIsNone(result)

    def test_send_includes_reply_to_when_configured(self) -> None:
        backend = ResendBackend()
        sent_payload: dict = {}

        def fake_send(payload: dict) -> dict:
            sent_payload.update(payload)
            return {"id": "msg_rpl"}

        with override_settings(
            POSTINO_RESEND_API_KEY="re_test_key",
            POSTINO_EMAIL_REPLY_TO="reply@example.com",
        ):
            self._patch_resend_send(fake_send)
            backend.send(
                to="user@example.com",
                subject="Test",
                html="<p>Hi</p>",
                text="Hi",
                headers=VALID_HEADERS,
            )

        self.assertEqual(sent_payload["reply_to"], "reply@example.com")

    def test_send_raises_when_api_key_missing(self) -> None:
        backend = ResendBackend()
        with override_settings(POSTINO_RESEND_API_KEY=""):
            with self.assertRaises(EmailBackendError) as ctx:
                backend.send(
                    to="user@example.com",
                    subject="Test",
                    html="<p>Hi</p>",
                    text="Hi",
                    headers=VALID_HEADERS,
                )
        self.assertIn("API key", str(ctx.exception))

    def test_send_does_not_call_provider_when_headers_missing(self) -> None:
        backend = ResendBackend()
        call_count = 0

        def fake_send(payload: dict) -> dict:
            nonlocal call_count
            call_count += 1
            return {"id": "msg_should_not_happen"}

        with override_settings(POSTINO_RESEND_API_KEY="re_test_key"):
            self._patch_resend_send(fake_send)
            with self.assertRaises(EmailBackendError):
                backend.send(
                    to="user@example.com",
                    subject="Test",
                    html="<p>Hi</p>",
                    text="Hi",
                    headers={},
                )

        self.assertEqual(call_count, 0, "Provider send should not be called with missing headers")

    def test_send_does_not_set_api_key_when_headers_missing(self) -> None:
        import resend

        backend = ResendBackend()
        resend.api_key = "original_marker"

        def fake_send(payload: dict) -> dict:
            return {"id": "msg_should_not_happen"}

        with override_settings(POSTINO_RESEND_API_KEY="re_new_key"):
            self._patch_resend_send(fake_send)
            with self.assertRaises(EmailBackendError):
                backend.send(
                    to="user@example.com",
                    subject="Test",
                    html="<p>Hi</p>",
                    text="Hi",
                    headers={},
                )

        self.assertEqual(
            resend.api_key,
            "original_marker",
            "API key must not be set when header validation fails first",
        )

    def _patch_resend_send(self, fake_send) -> None:
        import resend

        resend.Emails.send = fake_send

    def _assert_common_payload(
        self, payload: dict, expected_to: str, expected_subject: str
    ) -> None:
        from django.conf import settings

        self.assertIn(expected_to, payload["to"])
        self.assertEqual(payload["subject"], expected_subject)
        self.assertEqual(payload["html"], "<p>Hello</p>")
        self.assertEqual(payload["text"], "Hello")
        expected_from = f"{settings.POSTINO_EMAIL_FROM_NAME} <{settings.POSTINO_EMAIL_FROM_EMAIL}>"
        self.assertEqual(payload["from"], expected_from)
        self.assertEqual(payload["headers"]["List-Unsubscribe"], VALID_HEADERS["List-Unsubscribe"])
        self.assertEqual(
            payload["headers"]["List-Unsubscribe-Post"], VALID_HEADERS["List-Unsubscribe-Post"]
        )


# ---------------------------------------------------------------------------
# get_backend() factory
# ---------------------------------------------------------------------------


class TestGetBackend(TestCase):
    @override_settings(POSTINO_EMAIL_PROVIDER="console")
    def test_returns_console_backend(self) -> None:
        backend = get_backend()
        self.assertIsInstance(backend, ConsoleBackend)

    @override_settings(POSTINO_EMAIL_PROVIDER="resend")
    def test_returns_resend_backend(self) -> None:
        backend = get_backend()
        self.assertIsInstance(backend, ResendBackend)

    @override_settings(POSTINO_EMAIL_PROVIDER="smtp")
    def test_returns_smtp_backend(self) -> None:
        backend = get_backend()
        self.assertIsInstance(backend, SMTPBackend)

    @override_settings(POSTINO_EMAIL_PROVIDER="ses")
    def test_returns_ses_backend(self) -> None:
        from apps.core.email_backend import SESBackend

        backend = get_backend()
        self.assertIsInstance(backend, SESBackend)

    @override_settings(POSTINO_EMAIL_PROVIDER="mailgun")
    def test_returns_mailgun_backend(self) -> None:
        from apps.core.email_backend import MailgunBackend

        backend = get_backend()
        self.assertIsInstance(backend, MailgunBackend)

    @override_settings(POSTINO_EMAIL_PROVIDER="sendgrid")
    def test_raises_for_unknown_provider(self) -> None:
        with self.assertRaises(EmailBackendError) as ctx:
            get_backend()
        self.assertIn("Unknown email provider", str(ctx.exception))
        self.assertIn("sendgrid", str(ctx.exception))
