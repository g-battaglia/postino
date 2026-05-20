"""Pluggable email provider abstraction.

Every outgoing email MUST include RFC 8058 unsubscribe headers. The shared
validation helper enforces this invariant before any provider call, so a
missing header always fails loudly at the source.
"""

from __future__ import annotations

import sys
from abc import ABC, abstractmethod
from collections.abc import Mapping
from email.message import EmailMessage
from typing import Any

from django.conf import settings

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class EmailBackendError(Exception):
    """Raised when the email backend cannot send or is misconfigured."""


# ---------------------------------------------------------------------------
# Header validation (RFC 8058)
# ---------------------------------------------------------------------------

_LIST_UNSUBSCRIBE = "List-Unsubscribe"
_LIST_UNSUBSCRIBE_POST = "List-Unsubscribe-Post"
_ONE_CLICK_VALUE = "List-Unsubscribe=One-Click"


def _validate_unsubscribe_headers(headers: Mapping[str, str]) -> None:
    """Ensure required unsubscribe headers are present and valid.

    This is the single enforcement point for the invariant that every email
    sent by Postino carries RFC 8058 one-click unsubscribe headers.

    Raises
    ------
    EmailBackendError
        If ``List-Unsubscribe`` is missing or empty, or if
        ``List-Unsubscribe-Post`` does not contain the one-click value.
    """
    list_unsub = headers.get(_LIST_UNSUBSCRIBE, "").strip()
    if not list_unsub:
        raise EmailBackendError(
            "Missing required header: List-Unsubscribe. "
            "Every outgoing email must include an unsubscribe URL."
        )

    list_unsub_post = headers.get(_LIST_UNSUBSCRIBE_POST, "").strip()
    if _ONE_CLICK_VALUE not in list_unsub_post:
        raise EmailBackendError(
            f"Missing or invalid header: List-Unsubscribe-Post must contain "
            f"'{_ONE_CLICK_VALUE}', got '{list_unsub_post}'."
        )


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------


class EmailBackend(ABC):
    """Abstract interface for sending transactional/marketing emails."""

    @abstractmethod
    def send(
        self,
        to: str,
        subject: str,
        html: str,
        text: str,
        headers: Mapping[str, str],
    ) -> str | None:
        """Send an email and return the provider message ID, or ``None``.

        Parameters
        ----------
        to:
            Recipient email address.
        subject:
            Email subject line.
        html:
            HTML body.
        text:
            Plain-text body.
        headers:
            Extra SMTP headers (MUST include ``List-Unsubscribe`` and
            ``List-Unsubscribe-Post``).

        Raises
        ------
        EmailBackendError
            If required unsubscribe headers are missing or the provider
            rejects the send.
        """


# ---------------------------------------------------------------------------
# Console backend (development)
# ---------------------------------------------------------------------------


class ConsoleBackend(EmailBackend):
    """Write emails to stdout for local development.

    Produces a deterministic, human-readable representation including all
    headers so that developers can verify unsubscribe headers are present.
    """

    def send(
        self,
        to: str,
        subject: str,
        html: str,
        text: str,
        headers: Mapping[str, str],
    ) -> str | None:
        _validate_unsubscribe_headers(headers)

        lines = [
            "-" * 60,
            f"To: {to}",
            f"From: {settings.POSTINO_EMAIL_FROM_NAME} <{settings.POSTINO_EMAIL_FROM_EMAIL}>",
            f"Subject: {subject}",
        ]
        if settings.POSTINO_EMAIL_REPLY_TO:
            lines.append(f"Reply-To: {settings.POSTINO_EMAIL_REPLY_TO}")

        for key, value in headers.items():
            lines.append(f"{key}: {value}")

        lines.append("")
        if text:
            lines.append(text)
        lines.append("")
        if html:
            lines.append(html)
        lines.append("-" * 60)

        sys.stdout.write("\n".join(lines) + "\n")
        sys.stdout.flush()
        return None


# ---------------------------------------------------------------------------
# Resend backend
# ---------------------------------------------------------------------------


class ResendBackend(EmailBackend):
    """Send emails via the Resend API (``resend`` Python SDK)."""

    def send(
        self,
        to: str,
        subject: str,
        html: str,
        text: str,
        headers: Mapping[str, str],
    ) -> str | None:
        _validate_unsubscribe_headers(headers)
        self._validate_api_key()

        payload = self._build_payload(to, subject, html, text, headers)

        import resend

        resend.api_key = settings.POSTINO_RESEND_API_KEY
        response = resend.Emails.send(payload)

        if isinstance(response, dict) and response.get("id"):
            return str(response["id"])
        if response and str(response).startswith("re_"):
            return str(response)
        return None

    def _validate_api_key(self) -> None:
        api_key = settings.POSTINO_RESEND_API_KEY
        if not api_key:
            raise EmailBackendError(
                "Resend API key is not configured. "
                "Set [email.resend] api_key in config.toml "
                "or the POSTINO_EMAIL__RESEND__API_KEY env var."
            )

    def _build_payload(
        self,
        to: str,
        subject: str,
        html: str,
        text: str,
        headers: Mapping[str, str],
    ) -> dict[str, Any]:
        from_email = settings.POSTINO_EMAIL_FROM_EMAIL
        from_name = settings.POSTINO_EMAIL_FROM_NAME

        payload: dict[str, Any] = {
            "from": f"{from_name} <{from_email}>",
            "to": [to],
            "subject": subject,
            "html": html,
            "text": text,
            "headers": dict(headers),
        }

        reply_to = settings.POSTINO_EMAIL_REPLY_TO
        if reply_to:
            payload["reply_to"] = reply_to

        return payload


# ---------------------------------------------------------------------------
# SMTP backend
# ---------------------------------------------------------------------------


class SMTPBackend(EmailBackend):
    """Send emails via SMTP using Django's core mail infrastructure.

    Reads SMTP settings from config.toml [email.smtp] section.
    Enforces RFC 8058 unsubscribe headers like all other backends.
    """

    def send(
        self,
        to: str,
        subject: str,
        html: str,
        text: str,
        headers: Mapping[str, str],
    ) -> str | None:
        _validate_unsubscribe_headers(headers)
        self._validate_config()

        from django.core.mail import EmailMultiAlternatives, get_connection

        from_email = self._build_from_header()
        reply_to = settings.POSTINO_EMAIL_REPLY_TO
        use_ssl = getattr(settings, "POSTINO_SMTP_USE_SSL", False)
        connection = get_connection(
            host=settings.POSTINO_SMTP_HOST,
            port=settings.POSTINO_SMTP_PORT,
            username=settings.POSTINO_SMTP_USERNAME or None,
            password=settings.POSTINO_SMTP_PASSWORD or None,
            use_tls=settings.POSTINO_SMTP_USE_TLS and not use_ssl,
            use_ssl=use_ssl,
        )

        email = EmailMultiAlternatives(
            subject=subject,
            body=text,
            from_email=from_email,
            to=[to],
            headers=dict(headers),
            connection=connection,
        )
        if html:
            email.attach_alternative(html, "text/html")
        if reply_to:
            email.reply_to = [reply_to]

        email.send()

        message_id = email.extra_headers.get("Message-ID", getattr(email, "message_id", None))
        return str(message_id) if message_id else None

    def _validate_config(self) -> None:
        host = getattr(settings, "POSTINO_SMTP_HOST", "")
        if not host:
            raise EmailBackendError(
                "SMTP host is not configured. "
                "Set [email.smtp] host in config.toml."
            )

    def _build_from_header(self) -> str:
        from_name = settings.POSTINO_EMAIL_FROM_NAME
        from_email = settings.POSTINO_EMAIL_FROM_EMAIL
        return f"{from_name} <{from_email}>"


# ---------------------------------------------------------------------------
# SES backend
# ---------------------------------------------------------------------------


class SESBackend(EmailBackend):
    """Send emails via Amazon SES using boto3.

    Reads SES settings from config.toml [email.ses] section.
    Enforces RFC 8058 unsubscribe headers like all other backends.
    """

    def send(
        self,
        to: str,
        subject: str,
        html: str,
        text: str,
        headers: Mapping[str, str],
    ) -> str | None:
        _validate_unsubscribe_headers(headers)
        self._validate_config()

        try:
            import boto3
            from botocore.exceptions import BotoCoreError, ClientError
        except ImportError as exc:
            raise EmailBackendError(
                "boto3 is required for the SES backend. "
                "Install it with: pip install boto3"
            ) from exc

        client = boto3.client(
            "ses",
            aws_access_key_id=settings.POSTINO_SES_AWS_ACCESS_KEY_ID,
            aws_secret_access_key=settings.POSTINO_SES_AWS_SECRET_ACCESS_KEY,
            region_name=settings.POSTINO_SES_AWS_REGION,
        )

        raw_message = self._build_raw_message(to, subject, html, text, headers)

        try:
            response = client.send_raw_email(
                Source=self._build_from_header(),
                Destinations=[to],
                RawMessage={"Data": raw_message.as_bytes()},
            )
            return response.get("MessageId")
        except (BotoCoreError, ClientError) as exc:
            raise EmailBackendError(f"SES send failed: {exc}") from exc

    def _validate_config(self) -> None:
        key_id = getattr(settings, "POSTINO_SES_AWS_ACCESS_KEY_ID", "")
        secret = getattr(settings, "POSTINO_SES_AWS_SECRET_ACCESS_KEY", "")
        if not key_id or not secret:
            raise EmailBackendError(
                "SES credentials are not configured. "
                "Set [email.ses] aws_access_key_id and aws_secret_access_key in config.toml."
            )

    def _build_from_header(self) -> str:
        return f"{settings.POSTINO_EMAIL_FROM_NAME} <{settings.POSTINO_EMAIL_FROM_EMAIL}>"

    def _build_raw_message(
        self,
        to: str,
        subject: str,
        html: str,
        text: str,
        headers: Mapping[str, str],
    ) -> EmailMessage:
        message = EmailMessage()
        message["From"] = self._build_from_header()
        message["To"] = to
        message["Subject"] = subject
        reply_to = settings.POSTINO_EMAIL_REPLY_TO
        if reply_to:
            message["Reply-To"] = reply_to
        for key, value in headers.items():
            message[key] = value

        message.set_content(text or "")
        if html:
            message.add_alternative(html, subtype="html")
        return message


# ---------------------------------------------------------------------------
# Mailgun backend
# ---------------------------------------------------------------------------


class MailgunBackend(EmailBackend):
    """Send emails via the Mailgun HTTP API.

    Reads Mailgun settings from config.toml [email.mailgun] section.
    Enforces RFC 8058 unsubscribe headers like all other backends.
    """

    def send(
        self,
        to: str,
        subject: str,
        html: str,
        text: str,
        headers: Mapping[str, str],
    ) -> str | None:
        _validate_unsubscribe_headers(headers)
        self._validate_config()

        import base64
        import json as _json
        import urllib.parse
        import urllib.request

        domain = settings.POSTINO_MAILGUN_DOMAIN
        api_key = settings.POSTINO_MAILGUN_API_KEY

        data: dict[str, Any] = {
            "from": f"{settings.POSTINO_EMAIL_FROM_NAME} <{settings.POSTINO_EMAIL_FROM_EMAIL}>",
            "to": [to],
            "subject": subject,
        }
        if html:
            data["html"] = html
        if text:
            data["text"] = text

        reply_to = settings.POSTINO_EMAIL_REPLY_TO
        if reply_to:
            data["h:Reply-To"] = reply_to

        list_unsub = headers.get("List-Unsubscribe", "")
        if list_unsub:
            data["h:List-Unsubscribe"] = list_unsub
            data["h:List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"

        url = f"https://api.mailgun.net/v3/{domain}/messages"
        req = urllib.request.Request(
            url,
            data=urllib.parse.urlencode(data, doseq=True).encode("utf-8"),
            method="POST",
        )


        credentials = base64.b64encode(f"api:{api_key}".encode()).decode()
        req.add_header("Authorization", f"Basic {credentials}")

        try:
            with urllib.request.urlopen(req) as response:
                body = _json.loads(response.read().decode("utf-8"))
                return body.get("id", "").strip("<>")
        except urllib.error.HTTPError as exc:
            error_body = ""
            try:
                error_body = exc.read().decode("utf-8")
            except Exception:
                pass
            raise EmailBackendError(
                f"Mailgun API error {exc.code}: {error_body or exc.reason}"
            ) from exc
        except urllib.error.URLError as exc:
            raise EmailBackendError(f"Mailgun connection error: {exc.reason}") from exc

    def _validate_config(self) -> None:
        api_key = getattr(settings, "POSTINO_MAILGUN_API_KEY", "")
        domain = getattr(settings, "POSTINO_MAILGUN_DOMAIN", "")
        if not api_key or not domain:
            raise EmailBackendError(
                "Mailgun credentials are not configured. "
                "Set [email.mailgun] api_key and domain in config.toml."
            )


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

_UNSUPPORTED_PROVIDERS: dict[str, str] = {}

_BACKEND_MAP: dict[str, type[EmailBackend]] = {
    "console": ConsoleBackend,
    "resend": ResendBackend,
    "smtp": SMTPBackend,
    "ses": SESBackend,
    "mailgun": MailgunBackend,
}


def get_backend() -> EmailBackend:
    """Return the configured email backend instance.

    Reads ``settings.POSTINO_EMAIL_PROVIDER`` and instantiates the
    corresponding backend. Unsupported providers raise a clear error.

    Raises
    ------
    EmailBackendError
        If the configured provider has no backend implementation.
    """
    provider = settings.POSTINO_EMAIL_PROVIDER

    if provider in _BACKEND_MAP:
        return _BACKEND_MAP[provider]()

    if provider in _UNSUPPORTED_PROVIDERS:
        raise EmailBackendError(_UNSUPPORTED_PROVIDERS[provider])

    raise EmailBackendError(
        f"Unknown email provider '{provider}'. "
        f"Supported: {sorted(_BACKEND_MAP)}. "
        f"Not yet implemented: {sorted(_UNSUPPORTED_PROVIDERS)}."
    )
