"""Tests for the public unsubscribe flow and RFC 8058 one-click processing.

Covers: GET/POST manual form, CSRF enforcement, per-type/global/deletion
actions, one-click endpoint, token validation, suppression invariants,
header URL structure, and dark-pattern absence.
"""

from __future__ import annotations

import pytest
from django.test import Client

from apps.consent.models import ConsentRecord, EmailType, UnsubscribeEvent
from apps.consent.services import (
    build_unsubscribe_headers,
    build_unsubscribe_url,
    process_gdpr_deletion,
    process_global_unsubscribe,
    process_per_type_unsubscribe,
)
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
def client_csrf(db) -> Client:
    return Client(enforce_csrf_checks=True)


@pytest.fixture
def client_normal(db) -> Client:
    return Client()


def _unscoped_url(subscriber: Subscriber) -> str:
    token = generate_unsubscribe_token(subscriber.id)
    return f"/unsubscribe/?token={token}"


def _scoped_url(subscriber: Subscriber, email_type: EmailType) -> str:
    token = generate_unsubscribe_token(subscriber.id, email_type.slug)
    return f"/unsubscribe/?token={token}&type={email_type.slug}"


def _one_click_url(subscriber: Subscriber) -> str:
    token = generate_unsubscribe_token(subscriber.id)
    return f"/unsubscribe/one-click/?token={token}"


def _dark_pattern_terms() -> list[str]:
    return ["undo", "resubscribe", "are you sure", "sorry to see", "we'll miss"]


# ---------------------------------------------------------------------------
# GET unsubscribe page
# ---------------------------------------------------------------------------


class TestGetUnsubscribePage:
    def test_valid_unscoped_token_returns_200(
        self, client_normal: Client, subscriber: Subscriber,
    ) -> None:
        resp = client_normal.get(_unscoped_url(subscriber))
        assert resp.status_code == 200

    def test_no_script_tags(
        self, client_normal: Client, subscriber: Subscriber,
    ) -> None:
        resp = client_normal.get(_unscoped_url(subscriber))
        content = resp.content.decode()
        assert "<script" not in content.lower()

    def test_valid_scoped_token_returns_200(
        self, client_normal: Client, subscriber: Subscriber, marketing_type: EmailType,
    ) -> None:
        resp = client_normal.get(_scoped_url(subscriber, marketing_type))
        assert resp.status_code == 200

    def test_scoped_shows_email_type_name_in_label(
        self, client_normal: Client, subscriber: Subscriber, marketing_type: EmailType,
    ) -> None:
        resp = client_normal.get(_scoped_url(subscriber, marketing_type))
        content = resp.content.decode()
        assert "Weekly Digest" in content

    def test_unscoped_does_not_show_per_type(
        self, client_normal: Client, subscriber: Subscriber,
    ) -> None:
        resp = client_normal.get(_unscoped_url(subscriber))
        content = resp.content.decode()
        assert "per_type" not in content

    def test_invalid_token_returns_400(self, client_normal: Client, db) -> None:
        resp = client_normal.get("/unsubscribe/?token=invalid.token")
        assert resp.status_code == 400

    def test_tampered_token_returns_400(
        self, client_normal: Client, subscriber: Subscriber,
    ) -> None:
        token = generate_unsubscribe_token(subscriber.id)
        tampered = "AAAA" + token[4:]
        resp = client_normal.get(f"/unsubscribe/?token={tampered}")
        assert resp.status_code == 400

    def test_tampered_token_does_not_mutate(
        self, client_normal: Client, subscriber: Subscriber,
    ) -> None:
        token = generate_unsubscribe_token(subscriber.id)
        tampered = "AAAA" + token[4:]
        client_normal.get(f"/unsubscribe/?token={tampered}")
        subscriber.refresh_from_db()
        assert subscriber.status == Subscriber.Status.ACTIVE
        assert UnsubscribeEvent.objects.count() == 0

    def test_missing_token_returns_400(self, client_normal: Client, db) -> None:
        resp = client_normal.get("/unsubscribe/")
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# POST unsubscribe form (CSRF-protected)
# ---------------------------------------------------------------------------


class TestPostUnsubscribeForm:
    def test_csrf_required_on_post(
        self, client_csrf: Client, subscriber: Subscriber,
    ) -> None:
        token = generate_unsubscribe_token(subscriber.id)
        resp = client_csrf.post(
            "/unsubscribe/",
            {"token": token, "action": "global"},
        )
        assert resp.status_code == 403

    def test_per_type_withdraw_creates_records(
        self, client_normal: Client, subscriber: Subscriber, marketing_type: EmailType,
    ) -> None:
        token = generate_unsubscribe_token(subscriber.id, marketing_type.slug)
        resp = client_normal.post(
            "/unsubscribe/?type=" + marketing_type.slug,
            {
                "token": token,
                "email_type_slug": marketing_type.slug,
                "action": "per_type",
            },
        )
        assert resp.status_code == 200

        assert ConsentRecord.objects.filter(
            subscriber=subscriber,
            email_type=marketing_type,
            action=ConsentRecord.Action.WITHDRAW,
        ).count() == 1

        assert UnsubscribeEvent.objects.filter(
            subscriber=subscriber,
            email_type=marketing_type,
        ).count() == 1

        subscriber.refresh_from_db()
        assert subscriber.status == Subscriber.Status.ACTIVE

    def test_global_sets_status_unsubscribed(
        self, client_normal: Client, subscriber: Subscriber,
    ) -> None:
        token = generate_unsubscribe_token(subscriber.id)
        resp = client_normal.post(
            "/unsubscribe/",
            {"token": token, "action": "global"},
        )
        assert resp.status_code == 200

        subscriber.refresh_from_db()
        assert subscriber.status == Subscriber.Status.UNSUBSCRIBED

        assert UnsubscribeEvent.objects.filter(
            subscriber=subscriber,
            email_type__isnull=True,
            method="link",
        ).count() == 1

    def test_deletion_sets_status_deleted_and_purges(
        self, client_normal: Client, subscriber: Subscriber,
    ) -> None:
        token = generate_unsubscribe_token(subscriber.id)
        resp = client_normal.post(
            "/unsubscribe/",
            {"token": token, "action": "deletion"},
        )
        assert resp.status_code == 200

        subscriber.refresh_from_db()
        assert subscriber.status == Subscriber.Status.DELETED
        assert subscriber.name == ""
        assert subscriber.metadata == {}
        assert subscriber.ip_address is None
        assert subscriber.source_id == ""
        assert subscriber.double_optin_token is None
        assert subscriber.double_optin_confirmed_at is None
        assert subscriber.tags.count() == 0

        event = UnsubscribeEvent.objects.get(subscriber=subscriber, method="gdpr_deletion")
        assert event.email == "alice@example.com"

    def test_deletion_preserves_original_email_on_event(
        self, client_normal: Client, subscriber: Subscriber,
    ) -> None:
        token = generate_unsubscribe_token(subscriber.id)
        client_normal.post("/unsubscribe/", {"token": token, "action": "deletion"})

        event = UnsubscribeEvent.objects.get(method="gdpr_deletion")
        assert event.email == subscriber.email

    def test_repeated_global_unsubscribe_idempotent(
        self, client_normal: Client, subscriber: Subscriber,
    ) -> None:
        token = generate_unsubscribe_token(subscriber.id)
        client_normal.post("/unsubscribe/", {"token": token, "action": "global"})
        client_normal.post("/unsubscribe/", {"token": token, "action": "global"})

        subscriber.refresh_from_db()
        assert subscriber.status == Subscriber.Status.UNSUBSCRIBED
        assert UnsubscribeEvent.objects.filter(subscriber=subscriber).count() == 2

    def test_invalid_action_returns_400(
        self, client_normal: Client, subscriber: Subscriber,
    ) -> None:
        token = generate_unsubscribe_token(subscriber.id)
        resp = client_normal.post(
            "/unsubscribe/",
            {"token": token, "action": "unknown"},
        )
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# RFC 8058 one-click
# ---------------------------------------------------------------------------


class TestOneClickUnsubscribe:
    def test_valid_one_click_returns_200(
        self, client_normal: Client, subscriber: Subscriber,
    ) -> None:
        url = _one_click_url(subscriber)
        resp = client_normal.post(
            url,
            "List-Unsubscribe=One-Click",
            content_type="application/x-www-form-urlencoded",
        )
        assert resp.status_code == 200

        subscriber.refresh_from_db()
        assert subscriber.status == Subscriber.Status.UNSUBSCRIBED
        assert UnsubscribeEvent.objects.filter(
            subscriber=subscriber, method="one_click",
        ).count() == 1

    def test_one_click_no_csrf_required(
        self, client_csrf: Client, subscriber: Subscriber,
    ) -> None:
        url = _one_click_url(subscriber)
        resp = client_csrf.post(
            url,
            "List-Unsubscribe=One-Click",
            content_type="application/x-www-form-urlencoded",
        )
        assert resp.status_code == 200

    def test_one_click_missing_marker_returns_400(
        self, client_normal: Client, subscriber: Subscriber,
    ) -> None:
        url = _one_click_url(subscriber)
        resp = client_normal.post(
            url,
            "something-else",
            content_type="application/x-www-form-urlencoded",
        )
        assert resp.status_code == 400

        subscriber.refresh_from_db()
        assert subscriber.status == Subscriber.Status.ACTIVE
        assert UnsubscribeEvent.objects.count() == 0

    def test_one_click_invalid_token_returns_400(
        self, client_normal: Client, db,
    ) -> None:
        resp = client_normal.post(
            "/unsubscribe/one-click/?token=bad.token",
            "List-Unsubscribe=One-Click",
            content_type="application/x-www-form-urlencoded",
        )
        assert resp.status_code == 400

    def test_one_click_missing_token_returns_400(
        self, client_normal: Client, db,
    ) -> None:
        resp = client_normal.post(
            "/unsubscribe/one-click/",
            "List-Unsubscribe=One-Click",
            content_type="application/x-www-form-urlencoded",
        )
        assert resp.status_code == 400

    def test_one_click_creates_global_event(
        self, client_normal: Client, subscriber: Subscriber,
    ) -> None:
        url = _one_click_url(subscriber)
        client_normal.post(
            url,
            "List-Unsubscribe=One-Click",
            content_type="application/x-www-form-urlencoded",
        )
        event = UnsubscribeEvent.objects.get(subscriber=subscriber)
        assert event.email_type is None
        assert event.method == "one_click"

    def test_one_click_form_encoded_marker(
        self, client_normal: Client, subscriber: Subscriber,
    ) -> None:
        url = _one_click_url(subscriber)
        resp = client_normal.post(
            url,
            data="List-Unsubscribe=One-Click",
            content_type="application/x-www-form-urlencoded",
        )
        assert resp.status_code == 200
        subscriber.refresh_from_db()
        assert subscriber.status == Subscriber.Status.UNSUBSCRIBED
        assert UnsubscribeEvent.objects.filter(
            subscriber=subscriber, method="one_click"
        ).count() == 1

    def test_one_click_body_with_extra_prefix_rejected(
        self, client_normal: Client, subscriber: Subscriber,
    ) -> None:
        url = _one_click_url(subscriber)
        resp = client_normal.post(
            url,
            "junk-List-Unsubscribe=One-Click",
            content_type="application/x-www-form-urlencoded",
        )
        assert resp.status_code == 400
        subscriber.refresh_from_db()
        assert subscriber.status == Subscriber.Status.ACTIVE
        assert UnsubscribeEvent.objects.count() == 0

    def test_one_click_body_with_extra_suffix_rejected(
        self, client_normal: Client, subscriber: Subscriber,
    ) -> None:
        url = _one_click_url(subscriber)
        resp = client_normal.post(
            url,
            "List-Unsubscribe=One-Click&extra=stuff",
            content_type="application/x-www-form-urlencoded",
        )
        assert resp.status_code == 400
        subscriber.refresh_from_db()
        assert subscriber.status == Subscriber.Status.ACTIVE
        assert UnsubscribeEvent.objects.count() == 0


# ---------------------------------------------------------------------------
# build_unsubscribe_headers now points to /unsubscribe/one-click/
# ---------------------------------------------------------------------------


class TestUnsubscribeHeadersOneClickUrl:
    def test_header_url_points_to_one_click_endpoint(
        self, subscriber: Subscriber,
    ) -> None:
        headers = build_unsubscribe_headers(subscriber)
        url = headers["List-Unsubscribe"].strip("<>")
        assert "/unsubscribe/one-click/" in url
        assert "token=" in url

    def test_header_uses_unscoped_token(
        self, subscriber: Subscriber, marketing_type: EmailType,
    ) -> None:
        headers = build_unsubscribe_headers(subscriber, marketing_type)
        url = headers["List-Unsubscribe"].strip("<>")
        assert "type=" not in url

    def test_header_url_passes_backend_validation(
        self, subscriber: Subscriber,
    ) -> None:
        from apps.core.email_backend import _validate_unsubscribe_headers

        headers = build_unsubscribe_headers(subscriber)
        _validate_unsubscribe_headers(headers)  # should not raise

    def test_header_url_has_no_type_param(
        self, subscriber: Subscriber, marketing_type: EmailType,
    ) -> None:
        headers = build_unsubscribe_headers(subscriber, marketing_type)
        url = headers["List-Unsubscribe"].strip("<>")
        assert "type=" not in url

    def test_visible_url_still_uses_unsubscribe_path(
        self, subscriber: Subscriber,
    ) -> None:
        url = build_unsubscribe_url(subscriber)
        assert "/unsubscribe/" in url
        assert "/one-click/" not in url


# ---------------------------------------------------------------------------
# No dark patterns in rendered output
# ---------------------------------------------------------------------------


class TestNoDarkPatterns:
    @pytest.mark.parametrize("term", _dark_pattern_terms())
    def test_no_dark_pattern_terms_in_form(
        self, client_normal: Client, subscriber: Subscriber, term: str,
    ) -> None:
        resp = client_normal.get(_unscoped_url(subscriber))
        content = resp.content.decode().lower()
        assert term not in content

    @pytest.mark.parametrize("term", _dark_pattern_terms())
    def test_no_dark_pattern_terms_in_done(
        self, client_normal: Client, subscriber: Subscriber, term: str,
    ) -> None:
        token = generate_unsubscribe_token(subscriber.id)
        resp = client_normal.post(
            "/unsubscribe/",
            {"token": token, "action": "global"},
        )
        content = resp.content.decode().lower()
        assert term not in content


# ---------------------------------------------------------------------------
# Service-layer tests for processing functions
# ---------------------------------------------------------------------------


class TestProcessPerTypeUnsubscribe:
    def test_creates_consent_withdraw_and_event(
        self, subscriber: Subscriber, marketing_type: EmailType,
    ) -> None:
        event = process_per_type_unsubscribe(subscriber, marketing_type)

        assert ConsentRecord.objects.filter(
            subscriber=subscriber,
            email_type=marketing_type,
            action=ConsentRecord.Action.WITHDRAW,
        ).count() == 1

        assert event.email == subscriber.email
        assert event.email_type == marketing_type
        assert event.method == "link"

    def test_does_not_change_global_status(
        self, subscriber: Subscriber, marketing_type: EmailType,
    ) -> None:
        process_per_type_unsubscribe(subscriber, marketing_type)
        subscriber.refresh_from_db()
        assert subscriber.status == Subscriber.Status.ACTIVE


class TestProcessGlobalUnsubscribe:
    def test_sets_status_unsubscribed(self, subscriber: Subscriber) -> None:
        event = process_global_unsubscribe(subscriber)
        subscriber.refresh_from_db()
        assert subscriber.status == Subscriber.Status.UNSUBSCRIBED
        assert event.email_type is None
        assert event.method == "link"

    def test_idempotent_on_already_unsubscribed(self, subscriber: Subscriber) -> None:
        process_global_unsubscribe(subscriber)
        process_global_unsubscribe(subscriber)

        subscriber.refresh_from_db()
        assert subscriber.status == Subscriber.Status.UNSUBSCRIBED
        assert UnsubscribeEvent.objects.filter(subscriber=subscriber).count() == 2


class TestProcessGdprDeletion:
    def test_sets_status_deleted(self, subscriber: Subscriber) -> None:
        process_gdpr_deletion(subscriber)
        subscriber.refresh_from_db()
        assert subscriber.status == Subscriber.Status.DELETED

    def test_purges_personal_fields(self, subscriber: Subscriber) -> None:
        process_gdpr_deletion(subscriber)
        subscriber.refresh_from_db()
        assert subscriber.name == ""
        assert subscriber.metadata == {}
        assert subscriber.ip_address is None
        assert subscriber.source_id == ""
        assert subscriber.double_optin_token is None
        assert subscriber.double_optin_confirmed_at is None

    def test_clears_tags(
        self, subscriber: Subscriber, marketing_type: EmailType, db,
    ) -> None:
        from apps.subscribers.models import Tag

        tag = Tag.objects.create(name="vip", display_name="VIP")
        subscriber.tags.add(tag)
        assert subscriber.tags.count() == 1

        process_gdpr_deletion(subscriber)
        subscriber.refresh_from_db()
        assert subscriber.tags.count() == 0

    def test_preserves_original_email_on_event(self, subscriber: Subscriber) -> None:
        event = process_gdpr_deletion(subscriber)
        assert event.email == subscriber.email
        assert event.method == "gdpr_deletion"

    def test_idempotent_on_already_deleted(self, subscriber: Subscriber) -> None:
        process_gdpr_deletion(subscriber)
        process_gdpr_deletion(subscriber)

        subscriber.refresh_from_db()
        assert subscriber.status == Subscriber.Status.DELETED
        assert (
            UnsubscribeEvent.objects.filter(
                subscriber=subscriber, method="gdpr_deletion"
            ).count() == 2
        )


# ---------------------------------------------------------------------------
# Transaction atomic regression tests
# ---------------------------------------------------------------------------


class TestTransactionAtomicRollback:
    def test_global_unsubscribe_rolls_back_on_event_failure(
        self, subscriber: Subscriber,
    ) -> None:
        from unittest.mock import patch

        original_create = UnsubscribeEvent.objects.create
        call_count = 0

        def failing_create(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise Exception("simulated event creation failure")
            return original_create(*args, **kwargs)

        with patch.object(UnsubscribeEvent.objects, "create", side_effect=failing_create):
            with pytest.raises(Exception, match="simulated event creation failure"):
                process_global_unsubscribe(subscriber)

        subscriber.refresh_from_db()
        assert subscriber.status == Subscriber.Status.ACTIVE
        assert UnsubscribeEvent.objects.filter(subscriber=subscriber).count() == 0

    def test_per_type_unsubscribe_rolls_back_on_event_failure(
        self, subscriber: Subscriber, marketing_type: EmailType,
    ) -> None:
        from unittest.mock import patch

        original_create = UnsubscribeEvent.objects.create
        call_count = 0

        def failing_create(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise Exception("simulated event creation failure")
            return original_create(*args, **kwargs)

        with patch.object(UnsubscribeEvent.objects, "create", side_effect=failing_create):
            with pytest.raises(Exception, match="simulated event creation failure"):
                process_per_type_unsubscribe(subscriber, marketing_type)

        assert ConsentRecord.objects.filter(subscriber=subscriber).count() == 0
        assert UnsubscribeEvent.objects.filter(subscriber=subscriber).count() == 0

    def test_gdpr_deletion_rolls_back_on_save_failure(
        self, subscriber: Subscriber,
    ) -> None:
        from unittest.mock import patch

        original_save = Subscriber.save
        call_count = 0

        def failing_save(self_sub, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise Exception("simulated save failure")
            return original_save(self_sub, **kwargs)

        with patch.object(Subscriber, "save", failing_save):
            with pytest.raises(Exception, match="simulated save failure"):
                process_gdpr_deletion(subscriber)

        subscriber.refresh_from_db()
        assert subscriber.status == Subscriber.Status.ACTIVE
        assert subscriber.name == "Alice Smith"
        assert UnsubscribeEvent.objects.filter(subscriber=subscriber).count() == 0
