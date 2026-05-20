"""Tests for the public preference center page.

Covers: GET rendering, token validation, per-type consent grants/withdrawals,
global unsubscribe, GDPR deletion, suppression invariants, dark-pattern
absence, and token URL structure.
"""

from __future__ import annotations

import pytest
from django.test import Client

from apps.consent.models import ConsentRecord, EmailType, UnsubscribeEvent
from apps.consent.services import build_preferences_url
from apps.consent.tokens import generate_unsubscribe_token
from apps.subscribers.models import Subscriber

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def subscriber(db) -> Subscriber:
    return Subscriber.objects.create(
        email="alice@example.com",
        name="Alice Smith",
        status=Subscriber.Status.ACTIVE,
        source=Subscriber.Source.MANUAL,
        metadata={"plan": "pro"},
        ip_address="192.168.1.1",
        source_id="ext-123",
    )


@pytest.fixture
def marketing_type(db) -> EmailType:
    return EmailType.objects.create(
        slug="weekly_digest",
        name="Weekly Digest",
        is_transactional=False,
    )


@pytest.fixture
def product_type(db) -> EmailType:
    return EmailType.objects.create(
        slug="product_updates",
        name="Product Updates",
        is_transactional=False,
    )


@pytest.fixture
def transactional_type(db) -> EmailType:
    return EmailType.objects.create(
        slug="transactional",
        name="Transactional",
        is_transactional=True,
    )


@pytest.fixture
def client_normal(db) -> Client:
    return Client()


@pytest.fixture
def client_csrf(db) -> Client:
    return Client(enforce_csrf_checks=True)


def _pref_url(subscriber: Subscriber) -> str:
    token = generate_unsubscribe_token(subscriber.id)
    return f"/preferences/?token={token}"


def _dark_pattern_terms() -> list[str]:
    return ["undo", "resubscribe", "are you sure", "sorry to see", "we'll miss"]


def _grant(subscriber: Subscriber, email_type: EmailType) -> ConsentRecord:
    return ConsentRecord.objects.create(
        subscriber=subscriber,
        email_type=email_type,
        action=ConsentRecord.Action.GRANT,
        method="test",
    )


def _withdraw(subscriber: Subscriber, email_type: EmailType) -> ConsentRecord:
    return ConsentRecord.objects.create(
        subscriber=subscriber,
        email_type=email_type,
        action=ConsentRecord.Action.WITHDRAW,
        method="test",
    )


# ---------------------------------------------------------------------------
# GET preference center page
# ---------------------------------------------------------------------------


class TestGetPreferencePage:
    def test_valid_token_returns_200(
        self, client_normal: Client, subscriber: Subscriber,
    ) -> None:
        resp = client_normal.get(_pref_url(subscriber))
        assert resp.status_code == 200

    def test_no_script_tags(
        self, client_normal: Client, subscriber: Subscriber,
    ) -> None:
        resp = client_normal.get(_pref_url(subscriber))
        content = resp.content.decode()
        assert "<script" not in content.lower()

    def test_shows_subscriber_email(
        self, client_normal: Client, subscriber: Subscriber,
    ) -> None:
        resp = client_normal.get(_pref_url(subscriber))
        content = resp.content.decode()
        assert subscriber.email in content

    def test_shows_subscriber_name(
        self, client_normal: Client, subscriber: Subscriber,
    ) -> None:
        resp = client_normal.get(_pref_url(subscriber))
        content = resp.content.decode()
        assert "Alice Smith" in content

    def test_shows_active_status(
        self, client_normal: Client, subscriber: Subscriber,
    ) -> None:
        resp = client_normal.get(_pref_url(subscriber))
        content = resp.content.decode()
        assert "Active" in content

    def test_shows_email_types(
        self,
        client_normal: Client,
        subscriber: Subscriber,
        marketing_type: EmailType,
        product_type: EmailType,
    ) -> None:
        resp = client_normal.get(_pref_url(subscriber))
        content = resp.content.decode()
        assert "Weekly Digest" in content
        assert "Product Updates" in content

    def test_granted_type_checkbox_is_checked(
        self,
        client_normal: Client,
        subscriber: Subscriber,
        marketing_type: EmailType,
        product_type: EmailType,
    ) -> None:
        _grant(subscriber, marketing_type)
        _withdraw(subscriber, product_type)

        resp = client_normal.get(_pref_url(subscriber))
        content = resp.content.decode()

        assert f'name="type_{marketing_type.slug}" checked' in content
        assert f'name="type_{product_type.slug}" checked' not in content

    def test_missing_token_returns_400(
        self, client_normal: Client, db,
    ) -> None:
        resp = client_normal.get("/preferences/")
        assert resp.status_code == 400

    def test_invalid_token_returns_400(
        self, client_normal: Client, db,
    ) -> None:
        resp = client_normal.get("/preferences/?token=invalid.token")
        assert resp.status_code == 400

    def test_tampered_token_returns_400(
        self, client_normal: Client, subscriber: Subscriber,
    ) -> None:
        token = generate_unsubscribe_token(subscriber.id)
        tampered = "AAAA" + token[4:]
        resp = client_normal.get(f"/preferences/?token={tampered}")
        assert resp.status_code == 400

    def test_tampered_token_does_not_mutate(
        self, client_normal: Client, subscriber: Subscriber,
    ) -> None:
        token = generate_unsubscribe_token(subscriber.id)
        tampered = "AAAA" + token[4:]
        client_normal.get(f"/preferences/?token={tampered}")
        subscriber.refresh_from_db()
        assert subscriber.status == Subscriber.Status.ACTIVE
        assert ConsentRecord.objects.count() == 0
        assert UnsubscribeEvent.objects.count() == 0


# ---------------------------------------------------------------------------
# POST preference updates -- per-type consent changes
# ---------------------------------------------------------------------------


class TestPostPreferenceUpdates:
    def test_csrf_required_on_post(
        self, client_csrf: Client, subscriber: Subscriber,
    ) -> None:
        token = generate_unsubscribe_token(subscriber.id)
        resp = client_csrf.post(
            "/preferences/",
            {"token": token, "global_action": ""},
        )
        assert resp.status_code == 403

    def test_withdraw_type_creates_withdraw_record(
        self,
        client_normal: Client,
        subscriber: Subscriber,
        marketing_type: EmailType,
    ) -> None:
        _grant(subscriber, marketing_type)
        token = generate_unsubscribe_token(subscriber.id)
        resp = client_normal.post(
            "/preferences/",
            {"token": token, "global_action": ""},
        )
        assert resp.status_code == 200

        assert ConsentRecord.objects.filter(
            subscriber=subscriber,
            email_type=marketing_type,
            action=ConsentRecord.Action.WITHDRAW,
            method="preference_center",
        ).count() == 1

    def test_withdraw_does_not_modify_existing_record(
        self,
        client_normal: Client,
        subscriber: Subscriber,
        marketing_type: EmailType,
    ) -> None:
        grant = _grant(subscriber, marketing_type)
        grant_pk = grant.pk

        token = generate_unsubscribe_token(subscriber.id)
        client_normal.post(
            "/preferences/",
            {"token": token, "global_action": ""},
        )

        grant.refresh_from_db()
        assert grant.pk == grant_pk
        assert grant.action == ConsentRecord.Action.GRANT

    def test_grant_type_creates_grant_record(
        self,
        client_normal: Client,
        subscriber: Subscriber,
        marketing_type: EmailType,
    ) -> None:
        _withdraw(subscriber, marketing_type)
        token = generate_unsubscribe_token(subscriber.id)
        resp = client_normal.post(
            "/preferences/",
            {"token": token, f"type_{marketing_type.slug}": "on", "global_action": ""},
        )
        assert resp.status_code == 200

        assert ConsentRecord.objects.filter(
            subscriber=subscriber,
            email_type=marketing_type,
            action=ConsentRecord.Action.GRANT,
            method="preference_center",
        ).count() == 1

    def test_no_duplicate_consent_record_when_unchanged_grant(
        self,
        client_normal: Client,
        subscriber: Subscriber,
        marketing_type: EmailType,
    ) -> None:
        _grant(subscriber, marketing_type)
        initial_count = ConsentRecord.objects.filter(subscriber=subscriber).count()

        token = generate_unsubscribe_token(subscriber.id)
        resp = client_normal.post(
            "/preferences/",
            {"token": token, f"type_{marketing_type.slug}": "on", "global_action": ""},
        )
        assert resp.status_code == 200

        assert ConsentRecord.objects.filter(subscriber=subscriber).count() == initial_count

    def test_no_duplicate_consent_record_when_unchanged_withdraw(
        self,
        client_normal: Client,
        subscriber: Subscriber,
        marketing_type: EmailType,
    ) -> None:
        _withdraw(subscriber, marketing_type)
        initial_count = ConsentRecord.objects.filter(subscriber=subscriber).count()

        token = generate_unsubscribe_token(subscriber.id)
        resp = client_normal.post(
            "/preferences/",
            {"token": token, "global_action": ""},
        )
        assert resp.status_code == 200

        assert ConsentRecord.objects.filter(subscriber=subscriber).count() == initial_count

    def test_no_record_created_when_no_consent_history_and_unchecked(
        self,
        client_normal: Client,
        subscriber: Subscriber,
        marketing_type: EmailType,
    ) -> None:
        initial_count = ConsentRecord.objects.filter(subscriber=subscriber).count()

        token = generate_unsubscribe_token(subscriber.id)
        resp = client_normal.post(
            "/preferences/",
            {"token": token, "global_action": ""},
        )
        assert resp.status_code == 200

        assert ConsentRecord.objects.filter(subscriber=subscriber).count() == initial_count

    def test_multiple_type_changes_in_single_post(
        self,
        client_normal: Client,
        subscriber: Subscriber,
        marketing_type: EmailType,
        product_type: EmailType,
    ) -> None:
        _grant(subscriber, marketing_type)
        _withdraw(subscriber, product_type)

        token = generate_unsubscribe_token(subscriber.id)
        resp = client_normal.post(
            "/preferences/",
            {
                "token": token,
                "global_action": "",
                f"type_{marketing_type.slug}": "",
                f"type_{product_type.slug}": "on",
            },
        )
        assert resp.status_code == 200

        assert ConsentRecord.objects.filter(
            subscriber=subscriber,
            email_type=marketing_type,
            action=ConsentRecord.Action.WITHDRAW,
            method="preference_center",
        ).count() == 1

        assert ConsentRecord.objects.filter(
            subscriber=subscriber,
            email_type=product_type,
            action=ConsentRecord.Action.GRANT,
            method="preference_center",
        ).count() == 1


# ---------------------------------------------------------------------------
# Global unsubscribe from preference center
# ---------------------------------------------------------------------------


class TestGlobalUnsubscribeFromPreferences:
    def test_global_unsubscribe_sets_status(
        self, client_normal: Client, subscriber: Subscriber,
    ) -> None:
        token = generate_unsubscribe_token(subscriber.id)
        resp = client_normal.post(
            "/preferences/",
            {"token": token, "global_action": "global_unsubscribe"},
        )
        assert resp.status_code == 200

        subscriber.refresh_from_db()
        assert subscriber.status == Subscriber.Status.UNSUBSCRIBED

    def test_global_unsubscribe_creates_event(
        self, client_normal: Client, subscriber: Subscriber,
    ) -> None:
        token = generate_unsubscribe_token(subscriber.id)
        client_normal.post(
            "/preferences/",
            {"token": token, "global_action": "global_unsubscribe"},
        )

        event = UnsubscribeEvent.objects.get(subscriber=subscriber)
        assert event.email_type is None
        assert event.method == "preference_center"

    def test_global_unsubscribe_renders_done(
        self, client_normal: Client, subscriber: Subscriber,
    ) -> None:
        token = generate_unsubscribe_token(subscriber.id)
        resp = client_normal.post(
            "/preferences/",
            {"token": token, "global_action": "global_unsubscribe"},
        )
        content = resp.content.decode()
        assert "Unsubscribed" in content


# ---------------------------------------------------------------------------
# GDPR deletion from preference center
# ---------------------------------------------------------------------------


class TestGdprDeletionFromPreferences:
    def test_deletion_sets_status_deleted(
        self, client_normal: Client, subscriber: Subscriber,
    ) -> None:
        token = generate_unsubscribe_token(subscriber.id)
        resp = client_normal.post(
            "/preferences/",
            {"token": token, "global_action": "deletion"},
        )
        assert resp.status_code == 200

        subscriber.refresh_from_db()
        assert subscriber.status == Subscriber.Status.DELETED
        assert subscriber.name == ""
        assert subscriber.metadata == {}
        assert subscriber.ip_address is None
        assert subscriber.source_id == ""

    def test_deletion_creates_event(
        self, client_normal: Client, subscriber: Subscriber,
    ) -> None:
        token = generate_unsubscribe_token(subscriber.id)
        client_normal.post(
            "/preferences/",
            {"token": token, "global_action": "deletion"},
        )

        event = UnsubscribeEvent.objects.get(subscriber=subscriber)
        assert event.method == "preference_center_deletion"
        assert event.email == "alice@example.com"

    def test_deletion_renders_done(
        self, client_normal: Client, subscriber: Subscriber,
    ) -> None:
        token = generate_unsubscribe_token(subscriber.id)
        resp = client_normal.post(
            "/preferences/",
            {"token": token, "global_action": "deletion"},
        )
        content = resp.content.decode()
        assert "Data deleted" in content or "deleted" in content.lower()


# ---------------------------------------------------------------------------
# Suppressed subscriber invariants
# ---------------------------------------------------------------------------


class TestSuppressedSubscriberPreferences:
    def test_unsubscribed_shows_suppressed_status(
        self, client_normal: Client, subscriber: Subscriber,
    ) -> None:
        from apps.consent.services import process_global_unsubscribe
        process_global_unsubscribe(subscriber)
        subscriber.refresh_from_db()

        resp = client_normal.get(_pref_url(subscriber))
        content = resp.content.decode()
        assert "Unsubscribed" in content

    def test_unsubscribed_cannot_grant_per_type(
        self,
        client_normal: Client,
        subscriber: Subscriber,
        marketing_type: EmailType,
    ) -> None:
        _withdraw(subscriber, marketing_type)
        from apps.consent.services import process_global_unsubscribe
        process_global_unsubscribe(subscriber)
        subscriber.refresh_from_db()

        initial_count = ConsentRecord.objects.filter(subscriber=subscriber).count()
        token = generate_unsubscribe_token(subscriber.id)
        resp = client_normal.post(
            "/preferences/",
            {
                "token": token,
                "global_action": "",
                f"type_{marketing_type.slug}": "on",
            },
        )
        assert resp.status_code == 200
        assert ConsentRecord.objects.filter(subscriber=subscriber).count() == initial_count

    def test_deleted_cannot_grant_per_type(
        self,
        client_normal: Client,
        subscriber: Subscriber,
        marketing_type: EmailType,
    ) -> None:
        _withdraw(subscriber, marketing_type)
        from apps.consent.services import process_gdpr_deletion
        process_gdpr_deletion(subscriber)
        subscriber.refresh_from_db()

        initial_count = ConsentRecord.objects.filter(subscriber=subscriber).count()
        token = generate_unsubscribe_token(subscriber.id)
        resp = client_normal.post(
            "/preferences/",
            {
                "token": token,
                "global_action": "",
                f"type_{marketing_type.slug}": "on",
            },
        )
        assert resp.status_code == 200
        assert ConsentRecord.objects.filter(subscriber=subscriber).count() == initial_count

    @pytest.mark.parametrize(
        "status",
        ["unsubscribed", "bounced", "complained", "deleted"],
    )
    def test_suppressed_status_remains_after_preference_post(
        self,
        client_normal: Client,
        subscriber: Subscriber,
        marketing_type: EmailType,
        status: str,
    ) -> None:
        if status == "unsubscribed":
            from apps.consent.services import process_global_unsubscribe
            process_global_unsubscribe(subscriber)
        elif status == "deleted":
            from apps.consent.services import process_gdpr_deletion
            process_gdpr_deletion(subscriber)
        else:
            subscriber.status = status
            Subscriber.save(subscriber, update_fields=["status", "updated_at"])
        subscriber.refresh_from_db()

        token = generate_unsubscribe_token(subscriber.id)
        client_normal.post(
            "/preferences/",
            {
                "token": token,
                "global_action": "",
                f"type_{marketing_type.slug}": "on",
            },
        )
        subscriber.refresh_from_db()
        assert subscriber.status == status


# ---------------------------------------------------------------------------
# No dark patterns
# ---------------------------------------------------------------------------


class TestPreferencePageNoDarkPatterns:
    @pytest.mark.parametrize("term", _dark_pattern_terms())
    def test_no_dark_pattern_terms_in_preferences(
        self,
        client_normal: Client,
        subscriber: Subscriber,
        term: str,
    ) -> None:
        resp = client_normal.get(_pref_url(subscriber))
        content = resp.content.decode().lower()
        assert term not in content

    @pytest.mark.parametrize("term", _dark_pattern_terms())
    def test_no_dark_pattern_terms_in_done(
        self,
        client_normal: Client,
        subscriber: Subscriber,
        term: str,
    ) -> None:
        token = generate_unsubscribe_token(subscriber.id)
        resp = client_normal.post(
            "/preferences/",
            {"token": token, "global_action": "global_unsubscribe"},
        )
        content = resp.content.decode().lower()
        assert term not in content


# ---------------------------------------------------------------------------
# Token URL structure
# ---------------------------------------------------------------------------


class TestPreferenceUrlStructure:
    def test_build_preferences_url_contains_path(
        self, subscriber: Subscriber,
    ) -> None:
        url = build_preferences_url(subscriber)
        assert "/preferences/" in url

    def test_url_does_not_contain_email(
        self, subscriber: Subscriber,
    ) -> None:
        url = build_preferences_url(subscriber)
        assert subscriber.email not in url
        assert "@" not in url.split("?")[0]

    def test_url_contains_token_param(
        self, subscriber: Subscriber,
    ) -> None:
        url = build_preferences_url(subscriber)
        assert "token=" in url
