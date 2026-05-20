"""Tests for the double opt-in confirmation flow.

Covers: token generation/verification, cross-token rejection, service
initiation and confirmation, view GET handling, idempotency, and
consent record creation.
"""

from __future__ import annotations

import uuid

import pytest
from django.test import Client

from apps.consent.models import ConsentRecord
from apps.consent.services import (
    DoubleOptinError,
    confirm_double_optin,
    initiate_double_optin,
)
from apps.consent.tokens import (
    InvalidToken,
    generate_double_optin_token,
    generate_unsubscribe_token,
    verify_double_optin_token,
)
from apps.subscribers.models import Subscriber

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def pending_subscriber(db) -> Subscriber:
    return Subscriber.objects.create(
        email="pending@example.com",
        name="Pending User",
        status=Subscriber.Status.PENDING,
        source=Subscriber.Source.SIGNUP_FORM,
    )


@pytest.fixture
def active_subscriber(db) -> Subscriber:
    return Subscriber.objects.create(
        email="active@example.com",
        name="Active User",
        status=Subscriber.Status.ACTIVE,
        source=Subscriber.Source.MANUAL,
    )


@pytest.fixture
def suppressed_subscriber(db) -> Subscriber:
    return Subscriber.objects.create(
        email="suppressed@example.com",
        name="Suppressed User",
        status=Subscriber.Status.UNSUBSCRIBED,
        source=Subscriber.Source.MANUAL,
    )


@pytest.fixture
def client_normal(db) -> Client:
    return Client()


# ---------------------------------------------------------------------------
# Token generation and verification
# ---------------------------------------------------------------------------


class TestDoubleOptinToken:
    def test_generate_and_verify_double_optin_token(self) -> None:
        uid = uuid.UUID("12345678-1234-5678-1234-567812345678")
        token = generate_double_optin_token(uid)
        result = verify_double_optin_token(token)
        assert result == uid

    def test_deterministic(self) -> None:
        uid = uuid.uuid4()
        assert generate_double_optin_token(uid) == generate_double_optin_token(uid)

    def test_accepts_uuid_string(self) -> None:
        uid = uuid.uuid4()
        token = generate_double_optin_token(str(uid))
        assert verify_double_optin_token(token) == uid

    def test_double_optin_token_rejects_unsubscribe_token(self) -> None:
        uid = uuid.uuid4()
        unsub_token = generate_unsubscribe_token(uid)
        with pytest.raises(InvalidToken, match="signature verification failed"):
            verify_double_optin_token(unsub_token)

    def test_unsubscribe_token_rejects_double_optin_token(self) -> None:
        uid = uuid.uuid4()
        optin_token = generate_double_optin_token(uid)
        with pytest.raises(InvalidToken, match="signature verification failed"):
            from apps.consent.tokens import verify_unsubscribe_token
            verify_unsubscribe_token(optin_token)

    def test_tampered_payload_fails(self) -> None:
        uid = uuid.uuid4()
        token = generate_double_optin_token(uid)
        payload, signature = token.split(".")
        tampered = "AAAA" + payload[4:] + "." + signature
        with pytest.raises(InvalidToken):
            verify_double_optin_token(tampered)

    def test_tampered_signature_fails(self) -> None:
        uid = uuid.uuid4()
        token = generate_double_optin_token(uid)
        payload, signature = token.split(".")
        tampered = payload + "." + "AAAA" + signature[4:]
        with pytest.raises(InvalidToken, match="signature verification failed"):
            verify_double_optin_token(tampered)

    def test_missing_dot_fails(self) -> None:
        with pytest.raises(InvalidToken, match="exactly one dot"):
            verify_double_optin_token("nodothere")

    def test_wrong_secret_fails(self) -> None:
        uid = uuid.uuid4()
        token = generate_double_optin_token(uid, secret="secret-a-at-least-32-chars-long!!")
        with pytest.raises(InvalidToken, match="signature verification failed"):
            verify_double_optin_token(token, secret="secret-b-at-least-32-chars-long!!")


# ---------------------------------------------------------------------------
# Service: initiate_double_optin
# ---------------------------------------------------------------------------


class TestInitiateDoubleOptin:
    def test_initiate_double_optin_sets_token_and_returns_url(
        self, pending_subscriber: Subscriber,
    ) -> None:
        url = initiate_double_optin(pending_subscriber)
        assert "/confirm/?token=" in url

        pending_subscriber.refresh_from_db()
        assert pending_subscriber.double_optin_token is not None

        token = url.split("token=")[1]
        assert verify_double_optin_token(token) == pending_subscriber.id

    def test_initiate_double_optin_requires_pending_status(
        self, active_subscriber: Subscriber,
    ) -> None:
        with pytest.raises(DoubleOptinError, match="pending"):
            initiate_double_optin(active_subscriber)

    def test_initiate_double_optin_rejects_suppressed(
        self, suppressed_subscriber: Subscriber,
    ) -> None:
        with pytest.raises(DoubleOptinError, match="pending"):
            initiate_double_optin(suppressed_subscriber)


# ---------------------------------------------------------------------------
# Service: confirm_double_optin
# ---------------------------------------------------------------------------


class TestConfirmDoubleOptin:
    def test_confirm_double_optin_activates_subscriber(
        self, pending_subscriber: Subscriber,
    ) -> None:
        assert pending_subscriber.status == Subscriber.Status.PENDING
        assert pending_subscriber.double_optin_confirmed_at is None

        confirm_double_optin(pending_subscriber)

        pending_subscriber.refresh_from_db()
        assert pending_subscriber.status == Subscriber.Status.ACTIVE
        assert pending_subscriber.double_optin_confirmed_at is not None
        assert pending_subscriber.double_optin_token is None

    def test_confirm_double_optin_creates_consent_record(
        self, pending_subscriber: Subscriber,
    ) -> None:
        confirm_double_optin(pending_subscriber)

        record = ConsentRecord.objects.get(subscriber=pending_subscriber)
        assert record.action == ConsentRecord.Action.GRANT
        assert record.method == "double_optin"
        assert record.email_type is None

    def test_confirm_double_optin_idempotent_if_already_active(
        self, active_subscriber: Subscriber,
    ) -> None:
        initial_count = ConsentRecord.objects.filter(
            subscriber=active_subscriber,
        ).count()

        confirm_double_optin(active_subscriber)

        assert ConsentRecord.objects.filter(
            subscriber=active_subscriber,
        ).count() == initial_count

    def test_confirm_double_optin_clears_stale_token_on_active(
        self, active_subscriber: Subscriber,
    ) -> None:
        active_subscriber.double_optin_token = generate_double_optin_token(
            active_subscriber.id
        )
        active_subscriber.save(update_fields=["double_optin_token", "updated_at"])
        assert active_subscriber.double_optin_token is not None

        confirm_double_optin(active_subscriber)

        active_subscriber.refresh_from_db()
        assert active_subscriber.double_optin_token is None

    def test_confirm_double_optin_rejects_suppressed(
        self, suppressed_subscriber: Subscriber,
    ) -> None:
        with pytest.raises(DoubleOptinError, match="suppressed"):
            confirm_double_optin(suppressed_subscriber)

    def test_confirm_double_optin_rejects_bounced(self, db) -> None:
        bounced = Subscriber.objects.create(
            email="bounced@example.com",
            status=Subscriber.Status.BOUNCED,
        )
        with pytest.raises(DoubleOptinError, match="suppressed"):
            confirm_double_optin(bounced)


# ---------------------------------------------------------------------------
# View: confirm_email_view
# ---------------------------------------------------------------------------


class TestConfirmEmailView:
    def test_confirm_view_get_success(
        self, client_normal: Client, pending_subscriber: Subscriber,
    ) -> None:
        url = initiate_double_optin(pending_subscriber)
        resp = client_normal.get(url)
        assert resp.status_code == 200

        content = resp.content.decode()
        assert "confirmed" in content.lower()

        pending_subscriber.refresh_from_db()
        assert pending_subscriber.status == Subscriber.Status.ACTIVE

    def test_confirm_view_no_script_tags(
        self, client_normal: Client, pending_subscriber: Subscriber,
    ) -> None:
        url = initiate_double_optin(pending_subscriber)
        resp = client_normal.get(url)
        content = resp.content.decode()
        assert "<script" not in content.lower()

    def test_confirm_view_invalid_token(
        self, client_normal: Client, db,
    ) -> None:
        resp = client_normal.get("/confirm/?token=invalid.token")
        assert resp.status_code == 200

        content = resp.content.decode()
        assert "invalid" in content.lower()

    def test_confirm_view_missing_token(
        self, client_normal: Client, db,
    ) -> None:
        resp = client_normal.get("/confirm/")
        assert resp.status_code == 200

        content = resp.content.decode()
        assert "invalid" in content.lower()

    def test_confirm_view_subscriber_already_active(
        self, client_normal: Client, active_subscriber: Subscriber,
    ) -> None:
        token = generate_double_optin_token(active_subscriber.id)
        active_subscriber.double_optin_token = token
        active_subscriber.save(update_fields=["double_optin_token", "updated_at"])

        resp = client_normal.get(f"/confirm/?token={token}")
        assert resp.status_code == 200

        content = resp.content.decode()
        assert "confirmed" in content.lower()

    def test_confirm_view_token_mismatch_rejected(
        self, client_normal: Client, pending_subscriber: Subscriber, db,
    ) -> None:
        initiate_double_optin(pending_subscriber)

        other_subscriber = Subscriber.objects.create(
            email="other@example.com",
            status=Subscriber.Status.PENDING,
        )
        different_token = generate_double_optin_token(other_subscriber.id)

        resp = client_normal.get(f"/confirm/?token={different_token}")
        assert resp.status_code == 200

        content = resp.content.decode()
        assert "invalid" in content.lower()

        pending_subscriber.refresh_from_db()
        assert pending_subscriber.status == Subscriber.Status.PENDING

    def test_confirm_view_creates_consent_record(
        self, client_normal: Client, pending_subscriber: Subscriber,
    ) -> None:
        url = initiate_double_optin(pending_subscriber)
        client_normal.get(url)

        assert ConsentRecord.objects.filter(
            subscriber=pending_subscriber,
            action=ConsentRecord.Action.GRANT,
            method="double_optin",
        ).count() == 1

    def test_confirm_view_post_not_allowed(
        self, client_normal: Client, pending_subscriber: Subscriber,
    ) -> None:
        url = initiate_double_optin(pending_subscriber)
        resp = client_normal.post(url)
        assert resp.status_code == 405
