"""Tests for the Resend webhook endpoint, event processing, and auto-suppression.

Covers:
- Signature verification: valid, invalid, missing headers, missing secret.
- Event persistence: WebhookEvent created for all valid events.
- EmailSend status updates: delivered, opened, clicked, bounced, complained.
- Auto-suppression: bounce and complaint suppress subscriber and create UnsubscribeEvent.
- Email fallback: suppress by email address when provider_message_id is absent.
- Unknown event types: stored but do not mutate subscriber or EmailSend.
"""

import json

import pytest
from django.test import Client, override_settings

from apps.campaigns.models import EmailSend
from apps.consent.models import EmailType, UnsubscribeEvent
from apps.subscribers.models import Subscriber
from apps.templates_mgr.models import EmailTemplate
from apps.webhooks.models import WebhookEvent
from apps.webhooks.services import process_resend_event
from apps.webhooks.signature import (
    SignatureVerificationError,
    build_signature_headers,
    verify_signature,
)

pytestmark = pytest.mark.django_db

_TEST_SECRET = "whsec_test_secret_key_1234567890abcdef"
_TEST_SECRET_RAW = "test_secret_key_for_raw_mode"


@pytest.fixture
def client() -> Client:
    return Client()


@pytest.fixture
def email_type(db: None) -> EmailType:
    return EmailType.objects.create(slug="newsletter", name="Newsletter")


@pytest.fixture
def template() -> EmailTemplate:
    return EmailTemplate.objects.create(
        name="Test",
        slug="test",
        subject_default="Hello",
        html_body="<p>Hi</p>",
    )


@pytest.fixture
def active_subscriber() -> Subscriber:
    return Subscriber.objects.create(
        email="ada@example.com",
        name="Ada",
        status=Subscriber.Status.ACTIVE,
    )


def _make_email_send(
    subscriber: Subscriber,
    email_type: EmailType,
    provider_message_id: str = "msg_123",
) -> EmailSend:
    return EmailSend.objects.create(
        subscriber=subscriber,
        email_type=email_type,
        subject_line_used="Test",
        provider_message_id=provider_message_id,
        status=EmailSend.Status.SENT,
    )


def _build_resend_payload(
    event_type: str,
    email_id: str = "msg_123",
    to: str = "ada@example.com",
) -> dict:
    return {
        "type": event_type,
        "data": {
            "email_id": email_id,
            "to": to,
        },
    }


def _signed_post(
    client: Client,
    url: str,
    payload: dict,
    secret: str = _TEST_SECRET,
) -> object:
    body = json.dumps(payload)
    headers = build_signature_headers(body=body, secret=secret)
    return client.post(
        url,
        data=body,
        content_type="application/json",
        HTTP_SVIX_ID=headers["svix-id"],
        HTTP_SVIX_TIMESTAMP=headers["svix-timestamp"],
        HTTP_SVIX_SIGNATURE=headers["svix-signature"],
    )


# ---------------------------------------------------------------------------
# Signature verification unit tests
# ---------------------------------------------------------------------------


class TestSignatureVerification:
    def test_valid_signature_passes(self) -> None:
        body = '{"type":"email.delivered"}'
        headers = build_signature_headers(body=body, secret=_TEST_SECRET)
        verify_signature(
            body=body,
            svix_id=headers["svix-id"],
            svix_timestamp=headers["svix-timestamp"],
            svix_signature=headers["svix-signature"],
            secret=_TEST_SECRET,
        )

    def test_invalid_signature_raises(self) -> None:
        body = '{"type":"email.delivered"}'
        headers = build_signature_headers(body=body, secret=_TEST_SECRET)
        with pytest.raises(SignatureVerificationError, match="No matching"):
            verify_signature(
                body=body,
                svix_id=headers["svix-id"],
                svix_timestamp=headers["svix-timestamp"],
                svix_signature="v1,bad_signature_hex",
                secret=_TEST_SECRET,
            )

    def test_missing_svix_id_raises(self) -> None:
        body = '{"type":"email.delivered"}'
        headers = build_signature_headers(body=body, secret=_TEST_SECRET)
        with pytest.raises(SignatureVerificationError, match="Missing"):
            verify_signature(
                body=body,
                svix_id="",
                svix_timestamp=headers["svix-timestamp"],
                svix_signature=headers["svix-signature"],
                secret=_TEST_SECRET,
            )

    def test_missing_secret_raises(self) -> None:
        body = '{"type":"email.delivered"}'
        headers = build_signature_headers(body=body, secret=_TEST_SECRET)
        with pytest.raises(SignatureVerificationError, match="No webhook"):
            verify_signature(
                body=body,
                svix_id=headers["svix-id"],
                svix_timestamp=headers["svix-timestamp"],
                svix_signature=headers["svix-signature"],
                secret="",
            )

    def test_tampered_body_raises(self) -> None:
        body = '{"type":"email.delivered"}'
        headers = build_signature_headers(body=body, secret=_TEST_SECRET)
        with pytest.raises(SignatureVerificationError, match="No matching"):
            verify_signature(
                body='{"type":"email.bounced"}',
                svix_id=headers["svix-id"],
                svix_timestamp=headers["svix-timestamp"],
                svix_signature=headers["svix-signature"],
                secret=_TEST_SECRET,
            )

    def test_raw_secret_string_works(self) -> None:
        body = '{"type":"email.delivered"}'
        headers = build_signature_headers(body=body, secret=_TEST_SECRET_RAW)
        verify_signature(
            body=body,
            svix_id=headers["svix-id"],
            svix_timestamp=headers["svix-timestamp"],
            svix_signature=headers["svix-signature"],
            secret=_TEST_SECRET_RAW,
        )

    def test_base64_svix_signature_validates(self) -> None:
        """Canonical Svix base64-encoded signature passes verification."""
        body = '{"type":"email.delivered"}'
        headers = build_signature_headers(body=body, secret=_TEST_SECRET)
        sig_value = headers["svix-signature"]
        assert sig_value.startswith("v1,")
        verify_signature(
            body=body,
            svix_id=headers["svix-id"],
            svix_timestamp=headers["svix-timestamp"],
            svix_signature=sig_value,
            secret=_TEST_SECRET,
        )

    def test_invalid_base64_signature_rejected(self) -> None:
        body = '{"type":"email.delivered"}'
        headers = build_signature_headers(body=body, secret=_TEST_SECRET)
        with pytest.raises(SignatureVerificationError, match="No matching"):
            verify_signature(
                body=body,
                svix_id=headers["svix-id"],
                svix_timestamp=headers["svix-timestamp"],
                svix_signature="v1,aW52YWxpZHNpZw==",
                secret=_TEST_SECRET,
            )


# ---------------------------------------------------------------------------
# Webhook endpoint tests
# ---------------------------------------------------------------------------


class TestResendWebhookEndpoint:
    @override_settings(POSTINO_RESEND_WEBHOOK_SIGNING_SECRET=_TEST_SECRET)
    def test_valid_delivered_webhook_stores_event_and_updates_email_send(
        self, client: Client, email_type: EmailType, active_subscriber: Subscriber,
    ) -> None:
        email_send = _make_email_send(active_subscriber, email_type)
        payload = _build_resend_payload("email.delivered", email_id="msg_123")

        response = _signed_post(client, "/webhooks/resend/", payload)
        assert response.status_code == 200
        data = json.loads(response.content)
        assert data["ok"] is True

        event = WebhookEvent.objects.get()
        assert event.provider == "resend"
        assert event.event_type == "delivered"
        assert event.processed is True

        email_send.refresh_from_db()
        assert email_send.status == EmailSend.Status.DELIVERED
        assert email_send.delivered_at is not None

    @override_settings(POSTINO_RESEND_WEBHOOK_SIGNING_SECRET=_TEST_SECRET)
    def test_invalid_signature_rejected(
        self, client: Client, email_type: EmailType, active_subscriber: Subscriber,
    ) -> None:
        _make_email_send(active_subscriber, email_type)
        payload = _build_resend_payload("email.delivered")

        body = json.dumps(payload)
        response = client.post(
            "/webhooks/resend/",
            data=body,
            content_type="application/json",
            HTTP_SVIX_ID="msg_test",
            HTTP_SVIX_TIMESTAMP="1234567890",
            HTTP_SVIX_SIGNATURE="v1,invalid_signature",
        )

        assert response.status_code == 400
        assert WebhookEvent.objects.count() == 0

    @override_settings(POSTINO_RESEND_WEBHOOK_SIGNING_SECRET=_TEST_SECRET)
    def test_missing_signature_headers_rejected(
        self, client: Client,
    ) -> None:
        payload = _build_resend_payload("email.delivered")
        body = json.dumps(payload)
        response = client.post(
            "/webhooks/resend/",
            data=body,
            content_type="application/json",
        )

        assert response.status_code == 400
        assert WebhookEvent.objects.count() == 0

    @override_settings(POSTINO_RESEND_WEBHOOK_SIGNING_SECRET="")
    def test_missing_configured_secret_fails_closed(
        self, client: Client,
    ) -> None:
        payload = _build_resend_payload("email.delivered")
        response = _signed_post(client, "/webhooks/resend/", payload)

        assert response.status_code == 503
        data = json.loads(response.content)
        assert "not configured" in data["error"]
        assert WebhookEvent.objects.count() == 0

    @override_settings(POSTINO_RESEND_WEBHOOK_SIGNING_SECRET=_TEST_SECRET)
    def test_get_method_not_allowed(self, client: Client) -> None:
        response = client.get("/webhooks/resend/")
        assert response.status_code == 405

    @override_settings(POSTINO_RESEND_WEBHOOK_SIGNING_SECRET=_TEST_SECRET)
    def test_invalid_json_body_rejected(self, client: Client) -> None:
        headers = build_signature_headers(body="not json", secret=_TEST_SECRET)
        response = client.post(
            "/webhooks/resend/",
            data="not json",
            content_type="application/json",
            HTTP_SVIX_ID=headers["svix-id"],
            HTTP_SVIX_TIMESTAMP=headers["svix-timestamp"],
            HTTP_SVIX_SIGNATURE=headers["svix-signature"],
        )

        assert response.status_code == 400

    @override_settings(POSTINO_RESEND_WEBHOOK_SIGNING_SECRET=_TEST_SECRET)
    def test_unknown_event_type_stored_but_does_not_mutate(
        self, client: Client, email_type: EmailType, active_subscriber: Subscriber,
    ) -> None:
        email_send = _make_email_send(active_subscriber, email_type)
        payload = _build_resend_payload("email.unknown_event", email_id="msg_123")

        response = _signed_post(client, "/webhooks/resend/", payload)
        assert response.status_code == 200

        event = WebhookEvent.objects.get()
        assert event.processed is True

        email_send.refresh_from_db()
        assert email_send.status == EmailSend.Status.SENT


# ---------------------------------------------------------------------------
# Bounce auto-suppression
# ---------------------------------------------------------------------------


class TestBounceAutoSuppression:
    @override_settings(POSTINO_RESEND_WEBHOOK_SIGNING_SECRET=_TEST_SECRET)
    def test_bounce_auto_suppresses_subscriber(
        self, client: Client, email_type: EmailType, active_subscriber: Subscriber,
    ) -> None:
        _make_email_send(active_subscriber, email_type)
        payload = _build_resend_payload("email.bounced", email_id="msg_123")

        response = _signed_post(client, "/webhooks/resend/", payload)
        assert response.status_code == 200

        active_subscriber.refresh_from_db()
        assert active_subscriber.status == Subscriber.Status.BOUNCED

        unsub_events = UnsubscribeEvent.objects.filter(email="ada@example.com")
        assert unsub_events.count() == 1
        assert unsub_events.first().method == "webhook_bounce"
        assert unsub_events.first().email_type is None

    @override_settings(POSTINO_RESEND_WEBHOOK_SIGNING_SECRET=_TEST_SECRET)
    def test_bounce_updates_email_send_status(
        self, client: Client, email_type: EmailType, active_subscriber: Subscriber,
    ) -> None:
        email_send = _make_email_send(active_subscriber, email_type)
        payload = _build_resend_payload("email.bounced", email_id="msg_123")

        _signed_post(client, "/webhooks/resend/", payload)

        email_send.refresh_from_db()
        assert email_send.status == EmailSend.Status.BOUNCED
        assert email_send.bounced_at is not None

    @override_settings(POSTINO_RESEND_WEBHOOK_SIGNING_SECRET=_TEST_SECRET)
    def test_bounce_fallback_to_email_address(
        self, client: Client, email_type: EmailType, active_subscriber: Subscriber,
    ) -> None:
        """When provider_message_id does not match, suppress via email address."""
        payload = _build_resend_payload(
            "email.bounced",
            email_id="unknown_msg_id",
            to="ada@example.com",
        )

        response = _signed_post(client, "/webhooks/resend/", payload)
        assert response.status_code == 200

        active_subscriber.refresh_from_db()
        assert active_subscriber.status == Subscriber.Status.BOUNCED

        assert UnsubscribeEvent.objects.filter(email="ada@example.com").count() == 1

    @override_settings(POSTINO_RESEND_WEBHOOK_SIGNING_SECRET=_TEST_SECRET)
    def test_bounce_unknown_email_still_stores_event(
        self, client: Client,
    ) -> None:
        """Bounce for an email not in the system should still store the event."""
        payload = _build_resend_payload(
            "email.bounced",
            email_id="msg_unknown",
            to="nobody@example.com",
        )

        response = _signed_post(client, "/webhooks/resend/", payload)
        assert response.status_code == 200

        event = WebhookEvent.objects.get()
        assert event.processed is True
        assert Subscriber.objects.filter(email="nobody@example.com").count() == 0

    def test_bounce_via_service_directly(
        self, email_type: EmailType, active_subscriber: Subscriber,
    ) -> None:
        _make_email_send(active_subscriber, email_type)
        payload = {
            "type": "email.bounced",
            "data": {"email_id": "msg_123", "to": "ada@example.com"},
        }

        process_resend_event(payload)

        active_subscriber.refresh_from_db()
        assert active_subscriber.status == Subscriber.Status.BOUNCED
        assert UnsubscribeEvent.objects.filter(email="ada@example.com").count() == 1


# ---------------------------------------------------------------------------
# Complaint auto-suppression
# ---------------------------------------------------------------------------


class TestComplaintAutoSuppression:
    @override_settings(POSTINO_RESEND_WEBHOOK_SIGNING_SECRET=_TEST_SECRET)
    def test_complaint_auto_suppresses_subscriber(
        self, client: Client, email_type: EmailType, active_subscriber: Subscriber,
    ) -> None:
        _make_email_send(active_subscriber, email_type)
        payload = _build_resend_payload("email.complained", email_id="msg_123")

        response = _signed_post(client, "/webhooks/resend/", payload)
        assert response.status_code == 200

        active_subscriber.refresh_from_db()
        assert active_subscriber.status == Subscriber.Status.COMPLAINED

        unsub_events = UnsubscribeEvent.objects.filter(email="ada@example.com")
        assert unsub_events.count() == 1
        assert unsub_events.first().method == "webhook_complaint"
        assert unsub_events.first().email_type is None

    def test_complaint_via_service_directly(
        self, email_type: EmailType, active_subscriber: Subscriber,
    ) -> None:
        _make_email_send(active_subscriber, email_type)
        payload = {
            "type": "email.complained",
            "data": {"email_id": "msg_123", "to": "ada@example.com"},
        }

        process_resend_event(payload)

        active_subscriber.refresh_from_db()
        assert active_subscriber.status == Subscriber.Status.COMPLAINED
        assert UnsubscribeEvent.objects.filter(email="ada@example.com").count() == 1

    @override_settings(POSTINO_RESEND_WEBHOOK_SIGNING_SECRET=_TEST_SECRET)
    def test_complaint_updates_email_send_status(
        self, client: Client, email_type: EmailType, active_subscriber: Subscriber,
    ) -> None:
        email_send = _make_email_send(active_subscriber, email_type)
        payload = _build_resend_payload("email.complained", email_id="msg_123")

        _signed_post(client, "/webhooks/resend/", payload)

        email_send.refresh_from_db()
        assert email_send.status == EmailSend.Status.COMPLAINED
        assert email_send.complained_at is not None

    @override_settings(POSTINO_RESEND_WEBHOOK_SIGNING_SECRET=_TEST_SECRET)
    def test_complaint_fallback_to_email_in_payload(
        self, client: Client, email_type: EmailType, active_subscriber: Subscriber,
    ) -> None:
        payload = _build_resend_payload(
            "email.complained",
            email_id="no_match",
            to="ada@example.com",
        )

        response = _signed_post(client, "/webhooks/resend/", payload)
        assert response.status_code == 200

        active_subscriber.refresh_from_db()
        assert active_subscriber.status == Subscriber.Status.COMPLAINED

    @override_settings(POSTINO_RESEND_WEBHOOK_SIGNING_SECRET=_TEST_SECRET)
    def test_repeated_bounce_creates_additional_unsubscribe_events(
        self, client: Client, email_type: EmailType, active_subscriber: Subscriber,
    ) -> None:
        """Repeated webhook events may create additional UnsubscribeEvent rows."""
        _make_email_send(active_subscriber, email_type)
        payload = _build_resend_payload("email.bounced", email_id="msg_123")

        _signed_post(client, "/webhooks/resend/", payload)
        _signed_post(client, "/webhooks/resend/", payload)

        assert UnsubscribeEvent.objects.filter(email="ada@example.com").count() == 2


# ---------------------------------------------------------------------------
# Opened / Clicked events
# ---------------------------------------------------------------------------


class TestEngagementEvents:
    @override_settings(POSTINO_RESEND_WEBHOOK_SIGNING_SECRET=_TEST_SECRET)
    def test_opened_event_updates_email_send(
        self, client: Client, email_type: EmailType, active_subscriber: Subscriber,
    ) -> None:
        email_send = _make_email_send(active_subscriber, email_type)
        payload = _build_resend_payload("email.opened", email_id="msg_123")

        response = _signed_post(client, "/webhooks/resend/", payload)
        assert response.status_code == 200

        email_send.refresh_from_db()
        assert email_send.status == EmailSend.Status.OPENED
        assert email_send.opened_at is not None

        active_subscriber.refresh_from_db()
        assert active_subscriber.status == Subscriber.Status.ACTIVE

    @override_settings(POSTINO_RESEND_WEBHOOK_SIGNING_SECRET=_TEST_SECRET)
    def test_clicked_event_updates_email_send(
        self, client: Client, email_type: EmailType, active_subscriber: Subscriber,
    ) -> None:
        email_send = _make_email_send(active_subscriber, email_type)
        payload = _build_resend_payload("email.clicked", email_id="msg_123")

        response = _signed_post(client, "/webhooks/resend/", payload)
        assert response.status_code == 200

        email_send.refresh_from_db()
        assert email_send.status == EmailSend.Status.CLICKED
        assert email_send.clicked_at is not None

        active_subscriber.refresh_from_db()
        assert active_subscriber.status == Subscriber.Status.ACTIVE


# ---------------------------------------------------------------------------
# Payload extraction edge cases
# ---------------------------------------------------------------------------


class TestPayloadExtraction:
    def test_extracts_id_from_data_dot_id_field(self) -> None:
        payload = {"type": "email.delivered", "data": {"id": "alt_id"}}
        from apps.webhooks.services import _extract_provider_message_id

        assert _extract_provider_message_id(payload) == "alt_id"

    def test_extracts_id_from_top_level(self) -> None:
        payload = {"type": "email.delivered", "id": "top_id"}
        from apps.webhooks.services import _extract_provider_message_id

        assert _extract_provider_message_id(payload) == "top_id"

    def test_extracts_email_from_data_to(self) -> None:
        payload = {"type": "email.bounced", "data": {"to": "user@example.com"}}
        from apps.webhooks.services import _extract_email

        assert _extract_email(payload) == "user@example.com"

    def test_extracts_email_from_top_level_email(self) -> None:
        payload = {"type": "email.bounced", "email": "user@example.com"}
        from apps.webhooks.services import _extract_email

        assert _extract_email(payload) == "user@example.com"

    def test_empty_payload_returns_empty(self) -> None:
        payload: dict = {}
        from apps.webhooks.services import _extract_email, _extract_provider_message_id

        assert _extract_provider_message_id(payload) == ""
        assert _extract_email(payload) == ""

    def test_extracts_email_from_data_to_list(self) -> None:
        payload = {"type": "email.bounced", "data": {"to": ["user@example.com"]}}
        from apps.webhooks.services import _extract_email

        assert _extract_email(payload) == "user@example.com"

    def test_extracts_first_email_from_data_to_list(self) -> None:
        payload = {
            "type": "email.bounced",
            "data": {"to": ["first@example.com", "second@example.com"]},
        }
        from apps.webhooks.services import _extract_email

        assert _extract_email(payload) == "first@example.com"

    def test_extracts_email_from_data_to_tuple(self) -> None:
        payload = {"type": "email.bounced", "data": {"to": ("user@example.com",)}}
        from apps.webhooks.services import _extract_email

        assert _extract_email(payload) == "user@example.com"

    def test_extracts_email_from_top_level_to_list(self) -> None:
        payload = {"type": "email.bounced", "to": ["user@example.com"]}
        from apps.webhooks.services import _extract_email

        assert _extract_email(payload) == "user@example.com"

    def test_empty_list_yields_empty(self) -> None:
        payload = {"type": "email.bounced", "data": {"to": []}}
        from apps.webhooks.services import _extract_email

        assert _extract_email(payload) == ""

    def test_list_with_only_empty_strings_yields_empty(self) -> None:
        payload = {"type": "email.bounced", "data": {"to": ["", "  "]}}
        from apps.webhooks.services import _extract_email

        assert _extract_email(payload) == ""


# ---------------------------------------------------------------------------
# Already-suppressed subscriber protection
# ---------------------------------------------------------------------------


class TestAlreadySuppressedSubscriber:
    def test_bounce_on_deleted_subscriber_does_not_change_status(
        self, email_type: EmailType,
    ) -> None:
        deleted_sub = Subscriber.objects.create(
            email="deleted@example.com",
            name="Gone",
            status=Subscriber.Status.DELETED,
        )
        _make_email_send(deleted_sub, email_type, provider_message_id="msg_del")
        payload = {
            "type": "email.bounced",
            "data": {"email_id": "msg_del", "to": "deleted@example.com"},
        }

        process_resend_event(payload)

        deleted_sub.refresh_from_db()
        assert deleted_sub.status == Subscriber.Status.DELETED

        assert UnsubscribeEvent.objects.filter(email="deleted@example.com").count() == 1
        assert (
            UnsubscribeEvent.objects.filter(email="deleted@example.com").first().method
            == "webhook_bounce"
        )

    def test_complaint_on_deleted_subscriber_does_not_change_status(
        self, email_type: EmailType,
    ) -> None:
        deleted_sub = Subscriber.objects.create(
            email="deleted2@example.com",
            name="Gone",
            status=Subscriber.Status.DELETED,
        )
        _make_email_send(deleted_sub, email_type, provider_message_id="msg_del2")
        payload = {
            "type": "email.complained",
            "data": {"email_id": "msg_del2", "to": "deleted2@example.com"},
        }

        process_resend_event(payload)

        deleted_sub.refresh_from_db()
        assert deleted_sub.status == Subscriber.Status.DELETED

        assert UnsubscribeEvent.objects.filter(email="deleted2@example.com").count() == 1
        assert (
            UnsubscribeEvent.objects.filter(email="deleted2@example.com").first().method
            == "webhook_complaint"
        )

    def test_bounce_on_unsubscribed_subscriber_does_not_change_status(
        self, email_type: EmailType,
    ) -> None:
        unsub_sub = Subscriber.objects.create(
            email="unsub@example.com",
            name="Unsub",
            status=Subscriber.Status.UNSUBSCRIBED,
        )
        _make_email_send(unsub_sub, email_type, provider_message_id="msg_unsub")
        payload = {
            "type": "email.bounced",
            "data": {"email_id": "msg_unsub", "to": "unsub@example.com"},
        }

        process_resend_event(payload)

        unsub_sub.refresh_from_db()
        assert unsub_sub.status == Subscriber.Status.UNSUBSCRIBED

        assert UnsubscribeEvent.objects.filter(email="unsub@example.com").count() == 1

    def test_bounce_on_already_bounced_subscriber_keeps_status(
        self, email_type: EmailType,
    ) -> None:
        bounced_sub = Subscriber.objects.create(
            email="already@example.com",
            name="Bounced",
            status=Subscriber.Status.BOUNCED,
        )
        _make_email_send(bounced_sub, email_type, provider_message_id="msg_already")
        payload = {
            "type": "email.bounced",
            "data": {"email_id": "msg_already", "to": "already@example.com"},
        }

        process_resend_event(payload)

        bounced_sub.refresh_from_db()
        assert bounced_sub.status == Subscriber.Status.BOUNCED

        assert UnsubscribeEvent.objects.filter(email="already@example.com").count() == 1

    def test_email_extraction_from_list_suppresses_correct_subscriber(
        self, email_type: EmailType,
    ) -> None:
        sub = Subscriber.objects.create(
            email="list@example.com",
            status=Subscriber.Status.ACTIVE,
        )
        payload = {
            "type": "email.bounced",
            "data": {
                "email_id": "unknown_id",
                "to": ["list@example.com", "other@example.com"],
            },
        }

        process_resend_event(payload)

        sub.refresh_from_db()
        assert sub.status == Subscriber.Status.BOUNCED
        assert UnsubscribeEvent.objects.filter(email="list@example.com").count() == 1
