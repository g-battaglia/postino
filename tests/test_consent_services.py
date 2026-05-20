"""Tests for consent service functions.

Covers URL builders, RFC 8058 header construction, consent lookups,
suppression checks, and the combined can_send_to_subscriber gate.
"""

from __future__ import annotations

import pytest
from django.conf import settings

from apps.consent.models import ConsentRecord, EmailType, UnsubscribeEvent
from apps.consent.services import (
    build_preferences_url,
    build_unsubscribe_headers,
    build_unsubscribe_url,
    can_send_to_subscriber,
    get_latest_consent_action,
    has_marketing_consent,
    is_email_suppressed,
)
from apps.subscribers.models import Subscriber

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def subscriber(db) -> Subscriber:
    return Subscriber.objects.create(
        email="alice@example.com",
        name="Alice",
        status=Subscriber.Status.ACTIVE,
        source=Subscriber.Source.MANUAL,
    )


@pytest.fixture
def marketing_type(db) -> EmailType:
    return EmailType.objects.create(
        slug="weekly_digest",
        name="Weekly Digest",
        is_transactional=False,
    )


@pytest.fixture
def transactional_type(db) -> EmailType:
    return EmailType.objects.create(
        slug="transactional",
        name="Transactional",
        is_transactional=True,
    )


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


def _unsub_event(
    email: str,
    email_type: EmailType | None = None,
) -> UnsubscribeEvent:
    return UnsubscribeEvent.objects.create(
        email=email,
        email_type=email_type,
        method="test",
    )


# ---------------------------------------------------------------------------
# build_unsubscribe_url
# ---------------------------------------------------------------------------


class TestBuildUnsubscribeUrl:
    def test_includes_base_url(self, subscriber: Subscriber) -> None:
        url = build_unsubscribe_url(subscriber)
        assert url.startswith(settings.POSTINO_BASE_URL)

    def test_contains_unsubscribe_path(self, subscriber: Subscriber) -> None:
        url = build_unsubscribe_url(subscriber)
        assert "/unsubscribe/" in url
        assert "token=" in url

    def test_contains_token(self, subscriber: Subscriber) -> None:
        url = build_unsubscribe_url(subscriber)
        token_part = url.split("token=")[1].split("&")[0]
        assert len(token_part) > 10

    def test_no_email_in_url(self, subscriber: Subscriber) -> None:
        url = build_unsubscribe_url(subscriber)
        assert subscriber.email not in url
        assert "@" not in url.split("?")[0]

    def test_scoped_url_differs_from_unscoped(
        self,
        subscriber: Subscriber,
        marketing_type: EmailType,
    ) -> None:
        unscoped = build_unsubscribe_url(subscriber)
        scoped = build_unsubscribe_url(subscriber, marketing_type)
        assert unscoped != scoped

    def test_no_double_slash_after_base_url(self, subscriber: Subscriber) -> None:
        url = build_unsubscribe_url(subscriber)
        assert "//unsubscribe" not in url.replace("://", "")

    def test_unscoped_url_has_no_type_param(self, subscriber: Subscriber) -> None:
        url = build_unsubscribe_url(subscriber)
        assert "type=" not in url

    def test_scoped_url_includes_type_param(
        self,
        subscriber: Subscriber,
        marketing_type: EmailType,
    ) -> None:
        url = build_unsubscribe_url(subscriber, marketing_type)
        assert "type=weekly_digest" in url

    def test_scoped_url_still_has_token(
        self,
        subscriber: Subscriber,
        marketing_type: EmailType,
    ) -> None:
        url = build_unsubscribe_url(subscriber, marketing_type)
        assert "token=" in url
        assert "type=weekly_digest" in url


# ---------------------------------------------------------------------------
# build_preferences_url
# ---------------------------------------------------------------------------


class TestBuildPreferencesUrl:
    def test_includes_base_url(self, subscriber: Subscriber) -> None:
        url = build_preferences_url(subscriber)
        assert url.startswith(settings.POSTINO_BASE_URL)

    def test_contains_preferences_path(self, subscriber: Subscriber) -> None:
        url = build_preferences_url(subscriber)
        assert "/preferences/?token=" in url

    def test_no_email_in_url(self, subscriber: Subscriber) -> None:
        url = build_preferences_url(subscriber)
        assert subscriber.email not in url

    def test_contains_token(self, subscriber: Subscriber) -> None:
        url = build_preferences_url(subscriber)
        token_part = url.split("token=")[1]
        assert len(token_part) > 10


# ---------------------------------------------------------------------------
# build_unsubscribe_headers
# ---------------------------------------------------------------------------


class TestBuildUnsubscribeHeaders:
    def test_returns_both_required_keys(self, subscriber: Subscriber) -> None:
        headers = build_unsubscribe_headers(subscriber)
        assert "List-Unsubscribe" in headers
        assert "List-Unsubscribe-Post" in headers

    def test_list_unsubscribe_starts_with_angle_bracket(
        self,
        subscriber: Subscriber,
    ) -> None:
        headers = build_unsubscribe_headers(subscriber)
        assert headers["List-Unsubscribe"].startswith("<")
        assert headers["List-Unsubscribe"].endswith(">")

    def test_list_unsubscribe_post_is_one_click(
        self,
        subscriber: Subscriber,
    ) -> None:
        headers = build_unsubscribe_headers(subscriber)
        assert headers["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"

    def test_header_url_contains_token(
        self,
        subscriber: Subscriber,
        marketing_type: EmailType,
    ) -> None:
        headers = build_unsubscribe_headers(subscriber, marketing_type)
        url = headers["List-Unsubscribe"].strip("<>")
        assert "token=" in url
        assert "/unsubscribe/one-click/" in url

    def test_unscoped_header_has_no_type_param(
        self,
        subscriber: Subscriber,
    ) -> None:
        headers = build_unsubscribe_headers(subscriber)
        url = headers["List-Unsubscribe"].strip("<>")
        assert "type=" not in url

    def test_scoped_header_equals_unscoped(
        self,
        subscriber: Subscriber,
        marketing_type: EmailType,
    ) -> None:
        h1 = build_unsubscribe_headers(subscriber)
        h2 = build_unsubscribe_headers(subscriber, marketing_type)
        assert h1["List-Unsubscribe"] == h2["List-Unsubscribe"]


# ---------------------------------------------------------------------------
# get_latest_consent_action
# ---------------------------------------------------------------------------


class TestGetLatestConsentAction:
    def test_returns_none_when_no_records(
        self,
        subscriber: Subscriber,
        marketing_type: EmailType,
    ) -> None:
        assert get_latest_consent_action(subscriber, marketing_type) is None

    def test_returns_grant(self, subscriber: Subscriber, marketing_type: EmailType) -> None:
        _grant(subscriber, marketing_type)
        assert get_latest_consent_action(subscriber, marketing_type) == "grant"

    def test_returns_withdraw(self, subscriber: Subscriber, marketing_type: EmailType) -> None:
        _grant(subscriber, marketing_type)
        _withdraw(subscriber, marketing_type)
        assert get_latest_consent_action(subscriber, marketing_type) == "withdraw"

    def test_grant_then_withdraw_then_grant(
        self,
        subscriber: Subscriber,
        marketing_type: EmailType,
    ) -> None:
        _grant(subscriber, marketing_type)
        _withdraw(subscriber, marketing_type)
        _grant(subscriber, marketing_type)
        assert get_latest_consent_action(subscriber, marketing_type) == "grant"

    def test_withdraw_then_grant(
        self,
        subscriber: Subscriber,
        marketing_type: EmailType,
    ) -> None:
        _withdraw(subscriber, marketing_type)
        _grant(subscriber, marketing_type)
        assert get_latest_consent_action(subscriber, marketing_type) == "grant"

    def test_isolates_by_email_type(
        self,
        subscriber: Subscriber,
        marketing_type: EmailType,
        transactional_type: EmailType,
    ) -> None:
        _grant(subscriber, marketing_type)
        _withdraw(subscriber, transactional_type)
        assert get_latest_consent_action(subscriber, marketing_type) == "grant"
        assert get_latest_consent_action(subscriber, transactional_type) == "withdraw"

    def test_null_email_type_filter(
        self,
        subscriber: Subscriber,
        marketing_type: EmailType,
    ) -> None:
        ConsentRecord.objects.create(
            subscriber=subscriber,
            email_type=None,
            action=ConsentRecord.Action.GRANT,
            method="test",
        )
        _withdraw(subscriber, marketing_type)
        assert get_latest_consent_action(subscriber, None) == "grant"


# ---------------------------------------------------------------------------
# has_marketing_consent
# ---------------------------------------------------------------------------


class TestHasMarketingConsent:
    def test_false_when_no_consent(
        self,
        subscriber: Subscriber,
        marketing_type: EmailType,
    ) -> None:
        assert has_marketing_consent(subscriber, marketing_type) is False

    def test_true_after_grant(
        self,
        subscriber: Subscriber,
        marketing_type: EmailType,
    ) -> None:
        _grant(subscriber, marketing_type)
        assert has_marketing_consent(subscriber, marketing_type) is True

    def test_false_after_withdraw(
        self,
        subscriber: Subscriber,
        marketing_type: EmailType,
    ) -> None:
        _grant(subscriber, marketing_type)
        _withdraw(subscriber, marketing_type)
        assert has_marketing_consent(subscriber, marketing_type) is False


# ---------------------------------------------------------------------------
# is_email_suppressed
# ---------------------------------------------------------------------------


class TestIsEmailSuppressed:
    def test_not_suppressed_by_default(self, db) -> None:
        assert is_email_suppressed("nobody@example.com") is False

    def test_global_unsubscribe_suppresses(self, db) -> None:
        _unsub_event("alice@example.com", email_type=None)
        assert is_email_suppressed("alice@example.com") is True

    def test_global_unsubscribe_suppresses_with_type_filter(
        self,
        marketing_type: EmailType,
    ) -> None:
        _unsub_event("alice@example.com", email_type=None)
        assert is_email_suppressed("alice@example.com", marketing_type) is True

    def test_per_type_suppresses_matching_type(
        self,
        marketing_type: EmailType,
    ) -> None:
        _unsub_event("alice@example.com", email_type=marketing_type)
        assert is_email_suppressed("alice@example.com", marketing_type) is True

    def test_per_type_does_not_suppress_other_type(
        self,
        marketing_type: EmailType,
        transactional_type: EmailType,
    ) -> None:
        _unsub_event("alice@example.com", email_type=marketing_type)
        assert (
            is_email_suppressed("alice@example.com", transactional_type) is False
        )

    def test_case_insensitive(self, marketing_type: EmailType) -> None:
        _unsub_event("Alice@Example.COM", email_type=marketing_type)
        assert is_email_suppressed("alice@example.com", marketing_type) is True
        assert is_email_suppressed("ALICE@EXAMPLE.COM", marketing_type) is True

    def test_null_type_query_ignores_per_type_events(
        self,
        marketing_type: EmailType,
    ) -> None:
        _unsub_event("alice@example.com", email_type=marketing_type)
        assert is_email_suppressed("alice@example.com", email_type=None) is False


# ---------------------------------------------------------------------------
# can_send_to_subscriber
# ---------------------------------------------------------------------------


class TestCanSendToSubscriber:
    def test_active_with_consent_and_no_suppression(
        self,
        subscriber: Subscriber,
        marketing_type: EmailType,
    ) -> None:
        _grant(subscriber, marketing_type)
        assert can_send_to_subscriber(subscriber, marketing_type) is True

    def test_no_consent_means_no_marketing_email(
        self,
        subscriber: Subscriber,
        marketing_type: EmailType,
    ) -> None:
        assert can_send_to_subscriber(subscriber, marketing_type) is False

    def test_transactional_without_consent(
        self,
        subscriber: Subscriber,
        transactional_type: EmailType,
    ) -> None:
        assert can_send_to_subscriber(subscriber, transactional_type) is True

    def test_transactional_blocked_by_global_unsub(
        self,
        subscriber: Subscriber,
        transactional_type: EmailType,
    ) -> None:
        _unsub_event(subscriber.email, email_type=None)
        assert can_send_to_subscriber(subscriber, transactional_type) is False

    def test_transactional_blocked_by_per_type_unsub(
        self,
        subscriber: Subscriber,
        transactional_type: EmailType,
    ) -> None:
        _unsub_event(subscriber.email, email_type=transactional_type)
        assert can_send_to_subscriber(subscriber, transactional_type) is False

    @pytest.mark.parametrize(
        "status",
        ["unsubscribed", "bounced", "complained", "deleted"],
    )
    def test_suppressed_statuses_block_sending(
        self,
        subscriber: Subscriber,
        marketing_type: EmailType,
        status: str,
    ) -> None:
        _grant(subscriber, marketing_type)
        subscriber.status = status
        subscriber.save = lambda **kw: None  # skip suppression invariant
        assert can_send_to_subscriber(subscriber, marketing_type) is False

    def test_pending_status_blocks_sending(
        self,
        subscriber: Subscriber,
        marketing_type: EmailType,
    ) -> None:
        _grant(subscriber, marketing_type)
        subscriber.status = Subscriber.Status.PENDING
        assert can_send_to_subscriber(subscriber, marketing_type) is False

    def test_global_unsubscribe_blocks_all_types(
        self,
        subscriber: Subscriber,
        marketing_type: EmailType,
        transactional_type: EmailType,
    ) -> None:
        _grant(subscriber, marketing_type)
        _unsub_event(subscriber.email, email_type=None)
        assert can_send_to_subscriber(subscriber, marketing_type) is False
        assert can_send_to_subscriber(subscriber, transactional_type) is False

    def test_per_type_unsub_blocks_matching_but_not_others(
        self,
        subscriber: Subscriber,
        marketing_type: EmailType,
        transactional_type: EmailType,
    ) -> None:
        _grant(subscriber, marketing_type)
        _unsub_event(subscriber.email, email_type=marketing_type)
        assert can_send_to_subscriber(subscriber, marketing_type) is False
        assert can_send_to_subscriber(subscriber, transactional_type) is True

    def test_case_insensitive_email_in_unsubscribe_check(
        self,
        subscriber: Subscriber,
        marketing_type: EmailType,
    ) -> None:
        _grant(subscriber, marketing_type)
        _unsub_event(subscriber.email.upper(), email_type=None)
        assert can_send_to_subscriber(subscriber, marketing_type) is False

    def test_withdraw_latest_consent_blocks(
        self,
        subscriber: Subscriber,
        marketing_type: EmailType,
    ) -> None:
        _grant(subscriber, marketing_type)
        _withdraw(subscriber, marketing_type)
        assert can_send_to_subscriber(subscriber, marketing_type) is False
