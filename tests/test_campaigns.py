"""Tests for the campaigns app.

Covers Campaign and EmailSend model creation, status choices/defaults,
__str__, admin registration, and EmailSend logging service helpers.
"""

from __future__ import annotations

import pytest
from django.contrib.admin import AdminSite

from apps.campaigns.admin import CampaignAdmin, EmailSendAdmin
from apps.campaigns.models import Campaign, EmailSend
from apps.campaigns.services import (
    create_email_send,
    mark_delivered,
    mark_failed,
    mark_sent,
    update_from_webhook,
)
from apps.consent.models import EmailType
from apps.subscribers.models import Subscriber
from apps.templates_mgr.models import EmailTemplate

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def email_type(db: None) -> EmailType:
    return EmailType.objects.create(
        slug="newsletter",
        name="Newsletter",
    )


@pytest.fixture
def template(db: None) -> EmailTemplate:
    return EmailTemplate.objects.create(
        name="Weekly Digest",
        slug="weekly-digest",
        subject_default="Your weekly digest",
        html_body="<p>Hello {{ subscriber_name }}</p>",
    )


@pytest.fixture
def subscriber(db: None) -> Subscriber:
    return Subscriber.objects.create(
        email="ada@example.com",
        name="Ada Lovelace",
        status=Subscriber.Status.ACTIVE,
    )


@pytest.fixture
def campaign(email_type: EmailType, template: EmailTemplate) -> Campaign:
    return Campaign.objects.create(
        name="May 2026 Newsletter",
        email_type=email_type,
        template=template,
        subject_line="Your May digest",
    )


# ---------------------------------------------------------------------------
# Campaign model
# ---------------------------------------------------------------------------


class TestCampaignModel:
    def test_create_and_str(self, campaign: Campaign) -> None:
        assert str(campaign) == "May 2026 Newsletter [Draft]"

    def test_default_status_is_draft(self, email_type: EmailType, template: EmailTemplate) -> None:
        c = Campaign.objects.create(
            name="Test",
            email_type=email_type,
            template=template,
            subject_line="Test",
        )
        assert c.status == Campaign.Status.DRAFT

    def test_status_choices_enforced(self, campaign: Campaign) -> None:
        valid = {c.value for c in Campaign.Status}
        assert "draft" in valid
        assert "scheduled" in valid
        assert "sending" in valid
        assert "sent" in valid
        assert "cancelled" in valid

    def test_default_recipient_count_zero(self, campaign: Campaign) -> None:
        assert campaign.recipient_count == 0

    def test_scheduled_at_nullable(self, campaign: Campaign) -> None:
        assert campaign.scheduled_at is None

    def test_sent_at_nullable(self, campaign: Campaign) -> None:
        assert campaign.sent_at is None

    def test_audience_filter_default_empty_dict(self, campaign: Campaign) -> None:
        assert campaign.audience_filter == {}

    def test_timestamps_populated(self, campaign: Campaign) -> None:
        assert campaign.created_at is not None
        assert campaign.updated_at is not None

    def test_ordering_newest_first(
        self, email_type: EmailType, template: EmailTemplate,
    ) -> None:
        Campaign.objects.create(
            name="First", email_type=email_type, template=template, subject_line="A",
        )
        Campaign.objects.create(
            name="Second", email_type=email_type, template=template, subject_line="B",
        )
        names = list(Campaign.objects.values_list("name", flat=True))
        assert names == ["Second", "First"]

    def test_can_set_scheduled_status(self, campaign: Campaign) -> None:
        campaign.status = Campaign.Status.SCHEDULED
        campaign.save()
        campaign.refresh_from_db()
        assert campaign.status == Campaign.Status.SCHEDULED

    def test_protect_email_type_prevents_deletion(
        self, campaign: Campaign, email_type: EmailType,
    ) -> None:
        from django.db.models import ProtectedError

        with pytest.raises(ProtectedError):
            email_type.delete()

    def test_protect_template_prevents_deletion(
        self, campaign: Campaign, template: EmailTemplate,
    ) -> None:
        from django.db.models import ProtectedError

        with pytest.raises(ProtectedError):
            template.delete()


# ---------------------------------------------------------------------------
# EmailSend model
# ---------------------------------------------------------------------------


class TestEmailSendModel:
    def test_create_and_str(
        self, subscriber: Subscriber, campaign: Campaign, email_type: EmailType,
    ) -> None:
        es = EmailSend.objects.create(
            subscriber=subscriber,
            campaign=campaign,
            email_type=email_type,
            subject_line_used="Your May digest",
        )
        assert "EmailSend" in str(es)
        assert "Your May digest" in str(es)
        assert "Queued" in str(es)

    def test_default_status_is_queued(
        self, subscriber: Subscriber, campaign: Campaign, email_type: EmailType,
    ) -> None:
        es = EmailSend.objects.create(
            subscriber=subscriber,
            campaign=campaign,
            email_type=email_type,
            subject_line_used="Test",
        )
        assert es.status == EmailSend.Status.QUEUED

    def test_all_status_choices_present(self) -> None:
        values = {c.value for c in EmailSend.Status}
        expected = {
            "queued", "sent", "delivered", "opened",
            "clicked", "bounced", "complained", "failed",
        }
        assert values == expected

    def test_subscriber_nullable(
        self, campaign: Campaign, email_type: EmailType,
    ) -> None:
        es = EmailSend.objects.create(
            campaign=campaign,
            email_type=email_type,
            subject_line_used="Orphan send",
        )
        assert es.subscriber is None

    def test_campaign_nullable(
        self, subscriber: Subscriber, email_type: EmailType,
    ) -> None:
        es = EmailSend.objects.create(
            subscriber=subscriber,
            email_type=email_type,
            subject_line_used="No campaign",
        )
        assert es.campaign is None

    def test_provider_message_id_default_blank(
        self, subscriber: Subscriber, campaign: Campaign, email_type: EmailType,
    ) -> None:
        es = EmailSend.objects.create(
            subscriber=subscriber,
            campaign=campaign,
            email_type=email_type,
            subject_line_used="Test",
        )
        assert es.provider_message_id == ""

    def test_all_timestamp_fields_nullable(
        self, subscriber: Subscriber, campaign: Campaign, email_type: EmailType,
    ) -> None:
        es = EmailSend.objects.create(
            subscriber=subscriber,
            campaign=campaign,
            email_type=email_type,
            subject_line_used="Test",
        )
        assert es.sent_at is None
        assert es.delivered_at is None
        assert es.opened_at is None
        assert es.clicked_at is None
        assert es.bounced_at is None
        assert es.complained_at is None

    def test_error_message_default_blank(
        self, subscriber: Subscriber, campaign: Campaign, email_type: EmailType,
    ) -> None:
        es = EmailSend.objects.create(
            subscriber=subscriber,
            campaign=campaign,
            email_type=email_type,
            subject_line_used="Test",
        )
        assert es.error_message == ""

    def test_protect_email_type_on_email_send(
        self, subscriber: Subscriber, email_type: EmailType,
    ) -> None:
        from django.db.models import ProtectedError

        EmailSend.objects.create(
            subscriber=subscriber,
            email_type=email_type,
            subject_line_used="Test",
        )
        with pytest.raises(ProtectedError):
            email_type.delete()

    def test_set_null_subscriber_on_delete(
        self, subscriber: Subscriber, campaign: Campaign, email_type: EmailType,
    ) -> None:
        es = EmailSend.objects.create(
            subscriber=subscriber,
            campaign=campaign,
            email_type=email_type,
            subject_line_used="Test",
        )
        subscriber.delete()
        es.refresh_from_db()
        assert es.subscriber is None

    def test_set_null_campaign_on_delete(
        self, subscriber: Subscriber, campaign: Campaign, email_type: EmailType,
    ) -> None:
        es = EmailSend.objects.create(
            subscriber=subscriber,
            campaign=campaign,
            email_type=email_type,
            subject_line_used="Test",
        )
        campaign.delete()
        es.refresh_from_db()
        assert es.campaign is None


# ---------------------------------------------------------------------------
# Admin
# ---------------------------------------------------------------------------


class TestCampaignAdmin:
    def test_campaign_is_registered(self) -> None:
        from django.contrib import admin

        assert admin.site.is_registered(Campaign)

    def test_list_display_fields(self) -> None:
        ma = CampaignAdmin(Campaign, AdminSite())
        assert "name" in ma.list_display
        assert "status" in ma.list_display

    def test_search_fields(self) -> None:
        ma = CampaignAdmin(Campaign, AdminSite())
        assert "name" in ma.search_fields
        assert "subject_line" in ma.search_fields

    def test_list_filter(self) -> None:
        ma = CampaignAdmin(Campaign, AdminSite())
        assert "status" in ma.list_filter

    def test_readonly_timestamps(self) -> None:
        ma = CampaignAdmin(Campaign, AdminSite())
        assert "created_at" in ma.readonly_fields
        assert "updated_at" in ma.readonly_fields


class TestEmailSendAdmin:
    def test_emailsend_is_registered(self) -> None:
        from django.contrib import admin

        assert admin.site.is_registered(EmailSend)

    def test_list_display_fields(self) -> None:
        ma = EmailSendAdmin(EmailSend, AdminSite())
        assert "id" in ma.list_display
        assert "status" in ma.list_display
        assert "subscriber" in ma.list_display

    def test_search_fields(self) -> None:
        ma = EmailSendAdmin(EmailSend, AdminSite())
        assert "provider_message_id" in ma.search_fields

    def test_list_filter(self) -> None:
        ma = EmailSendAdmin(EmailSend, AdminSite())
        assert "status" in ma.list_filter


# ---------------------------------------------------------------------------
# Services — create_email_send
# ---------------------------------------------------------------------------


class TestCreateEmailSend:
    def test_creates_queued_record(
        self, subscriber: Subscriber, campaign: Campaign, email_type: EmailType,
    ) -> None:
        es = create_email_send(
            subscriber=subscriber,
            campaign=campaign,
            email_type=email_type,
            subject_line="Hello Ada",
        )
        assert es.pk is not None
        assert es.status == EmailSend.Status.QUEUED
        assert es.subscriber == subscriber
        assert es.campaign == campaign
        assert es.subject_line_used == "Hello Ada"

    def test_without_campaign(
        self, subscriber: Subscriber, email_type: EmailType,
    ) -> None:
        es = create_email_send(
            subscriber=subscriber,
            email_type=email_type,
            subject_line="Welcome",
        )
        assert es.campaign is None


# ---------------------------------------------------------------------------
# Services — mark_sent
# ---------------------------------------------------------------------------


class TestMarkSent:
    def test_transitions_to_sent(
        self, subscriber: Subscriber, campaign: Campaign, email_type: EmailType,
    ) -> None:
        es = create_email_send(
            subscriber=subscriber,
            campaign=campaign,
            email_type=email_type,
            subject_line="Test",
        )
        result = mark_sent(es, provider_message_id="msg_123")
        assert result.status == EmailSend.Status.SENT
        assert result.provider_message_id == "msg_123"
        assert result.sent_at is not None


# ---------------------------------------------------------------------------
# Services — mark_failed
# ---------------------------------------------------------------------------


class TestMarkFailed:
    def test_transitions_to_failed(
        self, subscriber: Subscriber, campaign: Campaign, email_type: EmailType,
    ) -> None:
        es = create_email_send(
            subscriber=subscriber,
            campaign=campaign,
            email_type=email_type,
            subject_line="Test",
        )
        result = mark_failed(es, error_message="SMTP timeout")
        assert result.status == EmailSend.Status.FAILED
        assert result.error_message == "SMTP timeout"


# ---------------------------------------------------------------------------
# Services — mark_delivered
# ---------------------------------------------------------------------------


class TestMarkDelivered:
    def test_transitions_to_delivered(
        self, subscriber: Subscriber, campaign: Campaign, email_type: EmailType,
    ) -> None:
        es = create_email_send(
            subscriber=subscriber,
            campaign=campaign,
            email_type=email_type,
            subject_line="Test",
        )
        mark_sent(es, provider_message_id="msg_abc")
        result = mark_delivered(es)
        assert result.status == EmailSend.Status.DELIVERED
        assert result.delivered_at is not None


# ---------------------------------------------------------------------------
# Services — update_from_webhook
# ---------------------------------------------------------------------------


class TestUpdateFromWebhook:
    def test_delivered_event(
        self, subscriber: Subscriber, campaign: Campaign, email_type: EmailType,
    ) -> None:
        es = create_email_send(
            subscriber=subscriber,
            campaign=campaign,
            email_type=email_type,
            subject_line="Test",
        )
        mark_sent(es, provider_message_id="msg_xyz")
        result = update_from_webhook("msg_xyz", "delivered")
        assert result is not None
        assert result.status == EmailSend.Status.DELIVERED
        assert result.delivered_at is not None

    def test_opened_event(
        self, subscriber: Subscriber, campaign: Campaign, email_type: EmailType,
    ) -> None:
        es = create_email_send(
            subscriber=subscriber,
            campaign=campaign,
            email_type=email_type,
            subject_line="Test",
        )
        mark_sent(es, provider_message_id="msg_open")
        update_from_webhook("msg_open", "delivered")
        result = update_from_webhook("msg_open", "opened")
        assert result is not None
        assert result.status == EmailSend.Status.OPENED
        assert result.opened_at is not None

    def test_bounced_event(
        self, subscriber: Subscriber, campaign: Campaign, email_type: EmailType,
    ) -> None:
        es = create_email_send(
            subscriber=subscriber,
            campaign=campaign,
            email_type=email_type,
            subject_line="Test",
        )
        mark_sent(es, provider_message_id="msg_bounce")
        result = update_from_webhook("msg_bounce", "bounced")
        assert result is not None
        assert result.status == EmailSend.Status.BOUNCED
        assert result.bounced_at is not None

    def test_complained_event(
        self, subscriber: Subscriber, campaign: Campaign, email_type: EmailType,
    ) -> None:
        es = create_email_send(
            subscriber=subscriber,
            campaign=campaign,
            email_type=email_type,
            subject_line="Test",
        )
        mark_sent(es, provider_message_id="msg_comp")
        result = update_from_webhook("msg_comp", "complained")
        assert result is not None
        assert result.status == EmailSend.Status.COMPLAINED
        assert result.complained_at is not None

    def test_unknown_message_id_returns_none(
        self, subscriber: Subscriber, email_type: EmailType,
    ) -> None:
        result = update_from_webhook("nonexistent_id", "delivered")
        assert result is None

    def test_unknown_event_type_returns_unchanged(
        self, subscriber: Subscriber, campaign: Campaign, email_type: EmailType,
    ) -> None:
        es = create_email_send(
            subscriber=subscriber,
            campaign=campaign,
            email_type=email_type,
            subject_line="Test",
        )
        mark_sent(es, provider_message_id="msg_unk")
        result = update_from_webhook("msg_unk", "unknown_event")
        assert result is not None
        assert result.status == EmailSend.Status.SENT

    def test_does_not_regress_status(
        self, subscriber: Subscriber, campaign: Campaign, email_type: EmailType,
    ) -> None:
        es = create_email_send(
            subscriber=subscriber,
            campaign=campaign,
            email_type=email_type,
            subject_line="Test",
        )
        mark_sent(es, provider_message_id="msg_reg")
        update_from_webhook("msg_reg", "delivered")
        result = update_from_webhook("msg_reg", "queued")
        assert result is not None
        assert result.status == EmailSend.Status.DELIVERED
