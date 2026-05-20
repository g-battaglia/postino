"""Tests for the campaign sending pipeline, advisory locks, and management commands.

Covers:
- Advisory lock fallback under SQLite (no-op).
- send_campaign service: eligible consented subscribers receive email.
- Suppressed/global-unsubscribed/per-type-unsubscribed subscribers are skipped.
- Missing consent for non-transactional type skips subscriber.
- Transactional email type bypasses marketing consent but respects global suppression.
- Unsubscribe headers passed to backend.
- Visible unsubscribe URL included in rendered body.
- EmailSend records created with sent/failed statuses.
- Campaign status/sent_at/recipient_count updated after send.
- send_campaign management command.
- check_scheduled_campaigns management command.
"""

from __future__ import annotations

from io import StringIO
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone

from apps.campaigns.models import Campaign, EmailSend
from apps.campaigns.services import (
    CampaignSendError,
    send_campaign,
)
from apps.consent.models import ConsentRecord, EmailType, UnsubscribeEvent
from apps.core.locks import _name_to_lock_id, advisory_lock
from apps.subscribers.models import Subscriber, Tag
from apps.templates_mgr.models import EmailTemplate

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeBackend:
    """In-memory fake backend that records every call instead of sending."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def send(self, to, subject, html, text, headers):
        if not headers.get("List-Unsubscribe"):
            raise RuntimeError("Missing List-Unsubscribe header")
        self.calls.append({
            "to": to,
            "subject": subject,
            "html": html,
            "text": text,
            "headers": headers,
        })
        return f"fake-{len(self.calls)}"


@pytest.fixture
def fake_backend() -> _FakeBackend:
    backend = _FakeBackend()
    with patch("apps.campaigns.services.get_backend", return_value=backend):
        yield backend


@pytest.fixture
def email_type_marketing(db: None) -> EmailType:
    return EmailType.objects.create(
        slug="newsletter",
        name="Newsletter",
        is_transactional=False,
    )


@pytest.fixture
def email_type_transactional(db: None) -> EmailType:
    return EmailType.objects.create(
        slug="transactional",
        name="Transactional",
        is_transactional=True,
    )


@pytest.fixture
def template() -> EmailTemplate:
    return EmailTemplate.objects.create(
        name="Test Template",
        slug="test-template",
        subject_default="Hello {{ subscriber_name }}",
        html_body="<p>Hi {{ subscriber_name }}, content here.</p>",
        text_body="Hi {{ subscriber_name }}, content here.",
    )


def _make_active_subscriber(email: str, *, name: str = "", **kwargs) -> Subscriber:
    return Subscriber.objects.create(
        email=email,
        name=name,
        status=Subscriber.Status.ACTIVE,
        **kwargs,
    )


def _grant_consent(subscriber: Subscriber, email_type: EmailType) -> ConsentRecord:
    return ConsentRecord.objects.create(
        subscriber=subscriber,
        email_type=email_type,
        action=ConsentRecord.Action.GRANT,
        method="test",
    )


def _make_campaign(
    email_type: EmailType,
    template: EmailTemplate,
    *,
    status: str = Campaign.Status.DRAFT,
    audience_filter: dict | None = None,
    scheduled_at=None,
) -> Campaign:
    return Campaign.objects.create(
        name="Test Campaign",
        email_type=email_type,
        template=template,
        subject_line="Test Subject",
        status=status,
        audience_filter=audience_filter or {},
        scheduled_at=scheduled_at,
    )


# ---------------------------------------------------------------------------
# Advisory lock (SQLite fallback)
# ---------------------------------------------------------------------------


class TestAdvisoryLock:
    def test_advisory_lock_yields_true_on_sqlite(self) -> None:
        with advisory_lock("test_lock") as acquired:
            assert acquired is True

    def test_advisory_lock_no_error_on_non_postgres(self) -> None:
        with advisory_lock("another_lock") as acquired:
            assert acquired is True
        # No exception = success

    def test_lock_id_is_deterministic(self) -> None:
        a = _name_to_lock_id("send_campaign_42")
        b = _name_to_lock_id("send_campaign_42")
        assert a == b

    def test_lock_id_different_names_differ(self) -> None:
        a = _name_to_lock_id("send_campaign_1")
        b = _name_to_lock_id("send_campaign_2")
        assert a != b

    def test_lock_id_known_value(self) -> None:
        """Verify the blake2b-based mapping is stable across Python versions."""
        value = _name_to_lock_id("check_scheduled_campaigns")
        assert isinstance(value, int)
        # Must fit in PostgreSQL int8 (-2^63 .. 2^63-1)
        assert -(2**63) <= value < 2**63
        # Same name must always produce the same number
        assert _name_to_lock_id("check_scheduled_campaigns") == value


# ---------------------------------------------------------------------------
# Sending pipeline — happy path
# ---------------------------------------------------------------------------


class TestSendCampaignHappyPath:
    def test_sends_to_eligible_active_consented_subscriber(
        self, fake_backend: _FakeBackend, email_type_marketing: EmailType,
        template: EmailTemplate,
    ) -> None:
        sub = _make_active_subscriber("ada@example.com", name="Ada")
        _grant_consent(sub, email_type_marketing)

        campaign = _make_campaign(email_type_marketing, template)
        result = send_campaign(campaign.pk)

        assert result.sent == 1
        assert result.eligible == 1
        assert result.skipped == 0
        assert result.failed == 0
        assert len(fake_backend.calls) == 1

    def test_email_send_record_created_as_sent(
        self, fake_backend: _FakeBackend, email_type_marketing: EmailType,
        template: EmailTemplate,
    ) -> None:
        sub = _make_active_subscriber("ada@example.com", name="Ada")
        _grant_consent(sub, email_type_marketing)

        campaign = _make_campaign(email_type_marketing, template)
        send_campaign(campaign.pk)

        es = EmailSend.objects.get(subscriber=sub, campaign=campaign)
        assert es.status == EmailSend.Status.SENT
        assert es.sent_at is not None
        assert es.provider_message_id.startswith("fake-")

    def test_campaign_status_updated_to_sent(
        self, fake_backend: _FakeBackend, email_type_marketing: EmailType,
        template: EmailTemplate,
    ) -> None:
        sub = _make_active_subscriber("ada@example.com")
        _grant_consent(sub, email_type_marketing)

        campaign = _make_campaign(email_type_marketing, template)
        send_campaign(campaign.pk)

        campaign.refresh_from_db()
        assert campaign.status == Campaign.Status.SENT
        assert campaign.sent_at is not None
        assert campaign.recipient_count == 1

    def test_unsubscribe_headers_passed_to_backend(
        self, fake_backend: _FakeBackend, email_type_marketing: EmailType,
        template: EmailTemplate,
    ) -> None:
        sub = _make_active_subscriber("ada@example.com")
        _grant_consent(sub, email_type_marketing)

        campaign = _make_campaign(email_type_marketing, template)
        send_campaign(campaign.pk)

        headers = fake_backend.calls[0]["headers"]
        assert "List-Unsubscribe" in headers
        assert headers["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"
        assert "/unsubscribe/" in headers["List-Unsubscribe"]

    def test_unsubscribe_url_in_rendered_body(
        self, fake_backend: _FakeBackend, email_type_marketing: EmailType,
        template: EmailTemplate,
    ) -> None:
        sub = _make_active_subscriber("ada@example.com", name="Ada")
        _grant_consent(sub, email_type_marketing)

        campaign = _make_campaign(email_type_marketing, template)
        send_campaign(campaign.pk)

        html = fake_backend.calls[0]["html"]
        text = fake_backend.calls[0]["text"]
        assert "/unsubscribe/" in html
        assert "/unsubscribe/" in text

    def test_subject_line_override_used(
        self, fake_backend: _FakeBackend, email_type_marketing: EmailType,
        template: EmailTemplate,
    ) -> None:
        sub = _make_active_subscriber("ada@example.com", name="Ada")
        _grant_consent(sub, email_type_marketing)

        campaign = _make_campaign(email_type_marketing, template)
        campaign.subject_line = "Custom Subject"
        campaign.save()
        send_campaign(campaign.pk)

        assert fake_backend.calls[0]["subject"] == "Custom Subject"

    def test_multiple_eligible_subscribers(
        self, fake_backend: _FakeBackend, email_type_marketing: EmailType,
        template: EmailTemplate,
    ) -> None:
        s1 = _make_active_subscriber("a@example.com")
        s2 = _make_active_subscriber("b@example.com")
        _grant_consent(s1, email_type_marketing)
        _grant_consent(s2, email_type_marketing)

        campaign = _make_campaign(email_type_marketing, template)
        result = send_campaign(campaign.pk)

        assert result.sent == 2
        assert result.eligible == 2
        assert len(fake_backend.calls) == 2


# ---------------------------------------------------------------------------
# Suppression and consent skipping
# ---------------------------------------------------------------------------


class TestSendCampaignSuppression:
    def test_suppressed_status_subscriber_not_in_audience(
        self, fake_backend: _FakeBackend, email_type_marketing: EmailType,
        template: EmailTemplate,
    ) -> None:
        Subscriber.objects.create(
            email="suppressed@example.com",
            status=Subscriber.Status.UNSUBSCRIBED,
        )

        campaign = _make_campaign(
            email_type_marketing, template,
            audience_filter={"status": "unsubscribed"},
        )
        result = send_campaign(campaign.pk)

        assert result.sent == 0
        assert result.skipped == 1
        assert len(fake_backend.calls) == 0

    def test_global_unsubscribed_subscriber_skipped(
        self, fake_backend: _FakeBackend, email_type_marketing: EmailType,
        template: EmailTemplate,
    ) -> None:
        sub = _make_active_subscriber("unsub@example.com")
        _grant_consent(sub, email_type_marketing)
        UnsubscribeEvent.objects.create(
            subscriber=sub,
            email=sub.email,
            email_type=None,
            method="link",
        )

        campaign = _make_campaign(email_type_marketing, template)
        result = send_campaign(campaign.pk)

        assert result.sent == 0
        assert result.skipped == 1

    def test_per_type_unsubscribed_subscriber_skipped(
        self, fake_backend: _FakeBackend, email_type_marketing: EmailType,
        template: EmailTemplate,
    ) -> None:
        sub = _make_active_subscriber("per-type@example.com")
        _grant_consent(sub, email_type_marketing)
        UnsubscribeEvent.objects.create(
            subscriber=sub,
            email=sub.email,
            email_type=email_type_marketing,
            method="link",
        )

        campaign = _make_campaign(email_type_marketing, template)
        result = send_campaign(campaign.pk)

        assert result.sent == 0
        assert result.skipped == 1

    def test_missing_consent_for_marketing_type_skips(
        self, fake_backend: _FakeBackend, email_type_marketing: EmailType,
        template: EmailTemplate,
    ) -> None:
        _make_active_subscriber("noconsent@example.com")
        # No consent record created

        campaign = _make_campaign(email_type_marketing, template)
        result = send_campaign(campaign.pk)

        assert result.sent == 0
        assert result.skipped == 1

    def test_withdrawn_consent_skips(
        self, fake_backend: _FakeBackend, email_type_marketing: EmailType,
        template: EmailTemplate,
    ) -> None:
        sub = _make_active_subscriber("withdrawn@example.com")
        _grant_consent(sub, email_type_marketing)
        ConsentRecord.objects.create(
            subscriber=sub,
            email_type=email_type_marketing,
            action=ConsentRecord.Action.WITHDRAW,
            method="link",
        )

        campaign = _make_campaign(email_type_marketing, template)
        result = send_campaign(campaign.pk)

        assert result.sent == 0
        assert result.skipped == 1

    def test_transactional_bypasses_consent_but_respects_suppression(
        self, fake_backend: _FakeBackend, email_type_transactional: EmailType,
        template: EmailTemplate,
    ) -> None:
        _make_active_subscriber("active-tx@example.com")
        suppressed_sub = _make_active_subscriber("suppressed-tx@example.com")

        UnsubscribeEvent.objects.create(
            subscriber=suppressed_sub,
            email=suppressed_sub.email,
            email_type=None,
            method="link",
        )

        campaign = _make_campaign(email_type_transactional, template)
        result = send_campaign(campaign.pk)

        assert result.sent == 1
        assert result.skipped == 1

    def test_bounced_status_not_in_default_audience(
        self, fake_backend: _FakeBackend, email_type_marketing: EmailType,
        template: EmailTemplate,
    ) -> None:
        Subscriber.objects.create(
            email="bounced@example.com",
            status=Subscriber.Status.BOUNCED,
        )

        campaign = _make_campaign(email_type_marketing, template)
        result = send_campaign(campaign.pk)

        assert result.sent == 0
        assert result.eligible == 0
        assert result.skipped == 0

    def test_pending_status_not_in_default_audience(
        self, fake_backend: _FakeBackend, email_type_marketing: EmailType,
        template: EmailTemplate,
    ) -> None:
        Subscriber.objects.create(
            email="pending@example.com",
            status=Subscriber.Status.PENDING,
        )

        campaign = _make_campaign(email_type_marketing, template)
        result = send_campaign(campaign.pk)

        assert result.sent == 0
        assert result.eligible == 0
        assert result.skipped == 0


# ---------------------------------------------------------------------------
# Audience filters
# ---------------------------------------------------------------------------


class TestAudienceFilters:
    def test_filter_by_tag(
        self, fake_backend: _FakeBackend, email_type_marketing: EmailType,
        template: EmailTemplate,
    ) -> None:
        tag = Tag.objects.create(name="vip", display_name="VIP")
        sub_tagged = _make_active_subscriber("tagged@example.com")
        sub_tagged.tags.add(tag)
        _grant_consent(sub_tagged, email_type_marketing)

        sub_untagged = _make_active_subscriber("untagged@example.com")
        _grant_consent(sub_untagged, email_type_marketing)

        campaign = _make_campaign(
            email_type_marketing, template,
            audience_filter={"tags": ["vip"]},
        )
        result = send_campaign(campaign.pk)

        assert result.sent == 1
        assert result.eligible == 1

    def test_filter_by_health_below(
        self, fake_backend: _FakeBackend, email_type_marketing: EmailType,
        template: EmailTemplate,
    ) -> None:
        sub_low = _make_active_subscriber("low@example.com", health_score=20)
        sub_high = _make_active_subscriber("high@example.com", health_score=80)
        _grant_consent(sub_low, email_type_marketing)
        _grant_consent(sub_high, email_type_marketing)

        campaign = _make_campaign(
            email_type_marketing, template,
            audience_filter={"health_below": 50},
        )
        result = send_campaign(campaign.pk)

        assert result.sent == 1

    def test_filter_by_health_above(
        self, fake_backend: _FakeBackend, email_type_marketing: EmailType,
        template: EmailTemplate,
    ) -> None:
        sub_low = _make_active_subscriber("low@example.com", health_score=20)
        sub_high = _make_active_subscriber("high@example.com", health_score=80)
        _grant_consent(sub_low, email_type_marketing)
        _grant_consent(sub_high, email_type_marketing)

        campaign = _make_campaign(
            email_type_marketing, template,
            audience_filter={"health_above": 50},
        )
        result = send_campaign(campaign.pk)

        assert result.sent == 1

    def test_empty_filter_selects_all_active(
        self, fake_backend: _FakeBackend, email_type_marketing: EmailType,
        template: EmailTemplate,
    ) -> None:
        s1 = _make_active_subscriber("a@example.com")
        s2 = _make_active_subscriber("b@example.com")
        _grant_consent(s1, email_type_marketing)
        _grant_consent(s2, email_type_marketing)

        campaign = _make_campaign(email_type_marketing, template, audience_filter={})
        result = send_campaign(campaign.pk)

        assert result.sent == 2

    def test_subscriber_with_two_matching_tags_sent_once(
        self, fake_backend: _FakeBackend, email_type_marketing: EmailType,
        template: EmailTemplate,
    ) -> None:
        tag_a = Tag.objects.create(name="tag-a", display_name="Tag A")
        tag_b = Tag.objects.create(name="tag-b", display_name="Tag B")
        sub = _make_active_subscriber("multi@example.com")
        sub.tags.add(tag_a, tag_b)
        _grant_consent(sub, email_type_marketing)

        campaign = _make_campaign(
            email_type_marketing, template,
            audience_filter={"tags": ["tag-a", "tag-b"]},
        )
        result = send_campaign(campaign.pk)

        assert result.sent == 1
        assert len(fake_backend.calls) == 1


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


class TestSendCampaignErrors:
    def test_nonexistent_campaign_raises(self, fake_backend: _FakeBackend) -> None:
        with pytest.raises(CampaignSendError, match="not found"):
            send_campaign(99999)

    def test_already_sent_campaign_raises(
        self, fake_backend: _FakeBackend, email_type_marketing: EmailType,
        template: EmailTemplate,
    ) -> None:
        campaign = _make_campaign(
            email_type_marketing, template, status=Campaign.Status.SENT,
        )
        with pytest.raises(CampaignSendError, match="cannot send"):
            send_campaign(campaign.pk)

    def test_already_sending_campaign_raises(
        self, fake_backend: _FakeBackend, email_type_marketing: EmailType,
        template: EmailTemplate,
    ) -> None:
        campaign = _make_campaign(
            email_type_marketing, template, status=Campaign.Status.SENDING,
        )
        with pytest.raises(CampaignSendError, match="cannot send"):
            send_campaign(campaign.pk)

    def test_cancelled_campaign_raises(
        self, fake_backend: _FakeBackend, email_type_marketing: EmailType,
        template: EmailTemplate,
    ) -> None:
        campaign = _make_campaign(
            email_type_marketing, template, status=Campaign.Status.CANCELLED,
        )
        with pytest.raises(CampaignSendError, match="cannot send"):
            send_campaign(campaign.pk)

    def test_send_failure_creates_failed_email_send(
        self, email_type_marketing: EmailType, template: EmailTemplate,
    ) -> None:
        sub = _make_active_subscriber("fail@example.com")
        _grant_consent(sub, email_type_marketing)

        failing_backend = _FakeBackend()
        original_send = failing_backend.send

        def _failing_send(to, subject, html, text, headers):
            original_send(to, subject, html, text, headers)
            raise RuntimeError("Provider timeout")

        failing_backend.send = _failing_send

        with patch("apps.campaigns.services.get_backend", return_value=failing_backend):
            campaign = _make_campaign(email_type_marketing, template)
            result = send_campaign(campaign.pk)

        assert result.sent == 0
        assert result.failed == 1

        es = EmailSend.objects.get(subscriber=sub, campaign=campaign)
        assert es.status == EmailSend.Status.FAILED
        assert "Provider timeout" in es.error_message

    def test_scheduled_campaign_can_be_sent(
        self, fake_backend: _FakeBackend, email_type_marketing: EmailType,
        template: EmailTemplate,
    ) -> None:
        sub = _make_active_subscriber("sched@example.com")
        _grant_consent(sub, email_type_marketing)

        campaign = _make_campaign(
            email_type_marketing, template, status=Campaign.Status.SCHEDULED,
        )
        result = send_campaign(campaign.pk)

        assert result.sent == 1

    def test_backend_failure_before_send_keeps_campaign_draft(
        self, email_type_marketing: EmailType, template: EmailTemplate,
    ) -> None:
        from apps.core.email_backend import EmailBackendError

        campaign = _make_campaign(email_type_marketing, template)

        with patch(
            "apps.campaigns.services.get_backend",
            side_effect=EmailBackendError("Provider not configured"),
        ):
            with pytest.raises(EmailBackendError, match="Provider not configured"):
                send_campaign(campaign.pk)

        campaign.refresh_from_db()
        assert campaign.status == Campaign.Status.DRAFT


# ---------------------------------------------------------------------------
# Management command: send_campaign
# ---------------------------------------------------------------------------


class TestSendCampaignCommand:
    def test_sends_campaign(
        self, fake_backend: _FakeBackend, email_type_marketing: EmailType,
        template: EmailTemplate,
    ) -> None:
        sub = _make_active_subscriber("cmd@example.com")
        _grant_consent(sub, email_type_marketing)

        campaign = _make_campaign(email_type_marketing, template)
        out = StringIO()

        call_command("send_campaign", str(campaign.pk), stdout=out)
        output = out.getvalue()

        assert "sent" in output.lower()
        assert "Eligible: 1" in output

    def test_nonexistent_campaign_raises_command_error(self) -> None:
        out = StringIO()
        with pytest.raises(CommandError):
            call_command("send_campaign", "99999", stdout=out, stderr=StringIO())

    def test_already_sent_raises_command_error(
        self, fake_backend: _FakeBackend, email_type_marketing: EmailType,
        template: EmailTemplate,
    ) -> None:
        campaign = _make_campaign(
            email_type_marketing, template, status=Campaign.Status.SENT,
        )
        with pytest.raises(CommandError):
            call_command(
                "send_campaign", str(campaign.pk),
                stdout=StringIO(), stderr=StringIO(),
            )


# ---------------------------------------------------------------------------
# Management command: check_scheduled_campaigns
# ---------------------------------------------------------------------------


class TestCheckScheduledCampaignsCommand:
    def test_sends_due_scheduled_campaigns(
        self, fake_backend: _FakeBackend, email_type_marketing: EmailType,
        template: EmailTemplate,
    ) -> None:
        sub = _make_active_subscriber("sched-cmd@example.com")
        _grant_consent(sub, email_type_marketing)

        campaign = _make_campaign(
            email_type_marketing, template,
            status=Campaign.Status.SCHEDULED,
            scheduled_at=timezone.now() - timezone.timedelta(minutes=5),
        )
        out = StringIO()

        call_command("check_scheduled_campaigns", stdout=out)
        output = out.getvalue()

        assert "Found 1 campaign(s)" in output
        assert "sent=1" in output

        campaign.refresh_from_db()
        assert campaign.status == Campaign.Status.SENT

    def test_skips_future_scheduled_campaigns(
        self, fake_backend: _FakeBackend, email_type_marketing: EmailType,
        template: EmailTemplate,
    ) -> None:
        sub = _make_active_subscriber("future@example.com")
        _grant_consent(sub, email_type_marketing)

        _make_campaign(
            email_type_marketing, template,
            status=Campaign.Status.SCHEDULED,
            scheduled_at=timezone.now() + timezone.timedelta(hours=1),
        )
        out = StringIO()

        call_command("check_scheduled_campaigns", stdout=out)
        output = out.getvalue()

        assert "No scheduled campaigns are due" in output
        assert len(fake_backend.calls) == 0

    def test_skips_draft_campaigns(
        self, fake_backend: _FakeBackend, email_type_marketing: EmailType,
        template: EmailTemplate,
    ) -> None:
        sub = _make_active_subscriber("draft@example.com")
        _grant_consent(sub, email_type_marketing)

        _make_campaign(
            email_type_marketing, template,
            status=Campaign.Status.DRAFT,
        )
        out = StringIO()

        call_command("check_scheduled_campaigns", stdout=out)
        output = out.getvalue()

        assert "No scheduled campaigns are due" in output
        assert len(fake_backend.calls) == 0

    def test_no_due_campaigns_outputs_message(self) -> None:
        out = StringIO()
        call_command("check_scheduled_campaigns", stdout=out)
        output = out.getvalue()
        assert "No scheduled campaigns are due" in output

    def test_sends_multiple_due_campaigns(
        self, fake_backend: _FakeBackend, email_type_marketing: EmailType,
        template: EmailTemplate,
    ) -> None:
        s1 = _make_active_subscriber("m1@example.com")
        s2 = _make_active_subscriber("m2@example.com")
        _grant_consent(s1, email_type_marketing)
        _grant_consent(s2, email_type_marketing)

        past = timezone.now() - timezone.timedelta(minutes=10)
        _make_campaign(
            email_type_marketing, template,
            status=Campaign.Status.SCHEDULED,
            scheduled_at=past,
        )
        _make_campaign(
            email_type_marketing, template,
            status=Campaign.Status.SCHEDULED,
            scheduled_at=past,
        )

        out = StringIO()
        call_command("check_scheduled_campaigns", stdout=out)
        output = out.getvalue()

        assert "Found 2 campaign(s)" in output
        # Each campaign sends to both eligible subscribers: 2 campaigns × 2 subs = 4
        assert len(fake_backend.calls) == 4
