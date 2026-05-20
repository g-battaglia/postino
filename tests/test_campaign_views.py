"""Tests for campaign dashboard views: list, create, detail, edit.

Covers admin-only access, filtering, pagination, form validation,
scheduled_at handling, audience_filter JSON validation, edit blocking
for sent/sending campaigns, and detail stats.
"""

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.utils import timezone

from apps.campaigns.models import Campaign, EmailSend
from apps.consent.models import EmailType
from apps.subscribers.models import Subscriber
from apps.templates_mgr.models import EmailTemplate


@pytest.fixture
def client():
    return Client()


@pytest.fixture
def admin_client(client):
    User.objects.create_superuser(
        username="admin", email="admin@test.com", password="testpass123"
    )
    client.login(username="admin", password="testpass123")
    return client


@pytest.fixture
def regular_client(client):
    User.objects.create_user(
        username="regular", email="user@test.com", password="testpass123"
    )
    client.login(username="regular", password="testpass123")
    return client


@pytest.fixture
def email_type(db) -> EmailType:
    return EmailType.objects.create(slug="newsletter", name="Newsletter")


@pytest.fixture
def template(db) -> EmailTemplate:
    return EmailTemplate.objects.create(
        name="Weekly Digest",
        slug="weekly-digest",
        subject_default="Your weekly digest",
        html_body="<p>Hello {{ subscriber_name }}</p>",
    )


@pytest.fixture
def draft_campaign(email_type, template) -> Campaign:
    return Campaign.objects.create(
        name="Test Campaign",
        email_type=email_type,
        template=template,
        subject_line="Test subject",
        status=Campaign.Status.DRAFT,
    )


@pytest.fixture
def scheduled_campaign(email_type, template) -> Campaign:
    return Campaign.objects.create(
        name="Scheduled Campaign",
        email_type=email_type,
        template=template,
        subject_line="Scheduled subject",
        status=Campaign.Status.SCHEDULED,
        scheduled_at=timezone.now() + timezone.timedelta(days=1),
    )


@pytest.fixture
def sent_campaign(email_type, template) -> Campaign:
    return Campaign.objects.create(
        name="Sent Campaign",
        email_type=email_type,
        template=template,
        subject_line="Sent subject",
        status=Campaign.Status.SENT,
        sent_at=timezone.now(),
        recipient_count=5,
    )


@pytest.fixture
def sample_campaigns(email_type, template):
    """Create campaigns in various statuses."""
    draft = Campaign.objects.create(
        name="Draft One",
        email_type=email_type,
        template=template,
        subject_line="Draft subject",
        status=Campaign.Status.DRAFT,
    )
    scheduled = Campaign.objects.create(
        name="Scheduled One",
        email_type=email_type,
        template=template,
        subject_line="Scheduled subject",
        status=Campaign.Status.SCHEDULED,
        scheduled_at=timezone.now() + timezone.timedelta(days=1),
    )
    sent = Campaign.objects.create(
        name="Sent One",
        email_type=email_type,
        template=template,
        subject_line="Sent subject",
        status=Campaign.Status.SENT,
        sent_at=timezone.now(),
        recipient_count=10,
    )
    return draft, scheduled, sent


# ── Admin-only access ──────────────────────────────────────────────────


@pytest.mark.django_db
class TestCampaignViewAccess:
    def test_list_redirects_unauthenticated(self, client):
        response = client.get("/campaigns/")
        assert response.status_code == 302
        assert "/admin/login/" in response.url

    def test_list_forbids_non_staff(self, regular_client):
        response = regular_client.get("/campaigns/")
        assert response.status_code == 302
        assert "/admin/login/" in response.url

    def test_create_redirects_unauthenticated(self, client):
        response = client.get("/campaigns/new/")
        assert response.status_code == 302
        assert "/admin/login/" in response.url

    def test_detail_redirects_unauthenticated(self, client, draft_campaign):
        response = client.get(f"/campaigns/{draft_campaign.pk}/")
        assert response.status_code == 302
        assert "/admin/login/" in response.url

    def test_edit_redirects_unauthenticated(self, client, draft_campaign):
        response = client.get(f"/campaigns/{draft_campaign.pk}/edit/")
        assert response.status_code == 302
        assert "/admin/login/" in response.url


# ── Campaign list ──────────────────────────────────────────────────────


@pytest.mark.django_db
class TestCampaignList:
    def test_list_renders_campaigns(self, admin_client, sample_campaigns):
        response = admin_client.get("/campaigns/")
        assert response.status_code == 200
        content = response.content.decode()
        assert "Draft One" in content
        assert "Scheduled One" in content
        assert "Sent One" in content

    def test_list_shows_total_count(self, admin_client, sample_campaigns):
        response = admin_client.get("/campaigns/")
        assert response.status_code == 200
        assert b"3 campaign" in response.content or b"3 campaigns total" in response.content

    def test_list_filter_by_status(self, admin_client, sample_campaigns):
        response = admin_client.get("/campaigns/?status=draft")
        assert response.status_code == 200
        content = response.content.decode()
        assert "Draft One" in content
        assert "Sent One" not in content

    def test_list_search_by_name(self, admin_client, sample_campaigns):
        response = admin_client.get("/campaigns/?q=Sent")
        assert response.status_code == 200
        content = response.content.decode()
        assert "Sent One" in content
        assert "Draft One" not in content

    def test_list_search_by_subject(self, admin_client, sample_campaigns):
        response = admin_client.get("/campaigns/?q=Draft+subject")
        assert response.status_code == 200
        content = response.content.decode()
        assert "Draft One" in content

    def test_list_empty(self, admin_client, db):
        response = admin_client.get("/campaigns/")
        assert response.status_code == 200
        content = response.content.decode()
        assert "No campaigns found" in content

    def test_list_pagination(self, admin_client, email_type, template):
        for i in range(30):
            Campaign.objects.create(
                name=f"Campaign {i}",
                email_type=email_type,
                template=template,
                subject_line=f"Subject {i}",
                status=Campaign.Status.DRAFT,
            )
        response = admin_client.get("/campaigns/?page=2")
        assert response.status_code == 200
        assert response.context["page_obj"].number == 2

    def test_list_pagination_preserves_filters(self, admin_client, email_type, template):
        for i in range(30):
            Campaign.objects.create(
                name=f"Campaign {i}",
                email_type=email_type,
                template=template,
                subject_line=f"Subject {i}",
                status=Campaign.Status.DRAFT,
            )
        response = admin_client.get("/campaigns/?q=Campaign&page=2")
        assert response.status_code == 200
        assert "q=Campaign" in response.context["filter_qs"]

    def test_list_status_pills_show_correct_counts(self, admin_client, sample_campaigns):
        response = admin_client.get("/campaigns/")
        assert response.status_code == 200
        pills = response.context["status_pills"]
        counts_by_status = {p["value"]: p["count"] for p in pills}
        assert counts_by_status.get("draft") == 1
        assert counts_by_status.get("scheduled") == 1
        assert counts_by_status.get("sent") == 1
        content = response.content.decode()
        assert ">1</span>" in content

    def test_list_status_pill_active_state(self, admin_client, sample_campaigns):
        response = admin_client.get("/campaigns/?status=draft")
        assert response.status_code == 200
        pills = response.context["status_pills"]
        draft_pill = next(p for p in pills if p["value"] == "draft")
        sent_pill = next(p for p in pills if p["value"] == "sent")
        assert draft_pill["active"] is True
        assert sent_pill["active"] is False

    def test_list_htmx_returns_partial_only(self, admin_client, sample_campaigns):
        response = admin_client.get(
            "/campaigns/", HTTP_HX_REQUEST="true"
        )
        assert response.status_code == 200
        content = response.content.decode()
        assert 'id="campaign-table"' in content
        assert "<html" not in content
        assert "sidebar" not in content
        assert "Main navigation" not in content

    def test_list_htmx_search_returns_partial(self, admin_client, sample_campaigns):
        response = admin_client.get(
            "/campaigns/?q=Draft", HTTP_HX_REQUEST="true"
        )
        assert response.status_code == 200
        content = response.content.decode()
        assert 'id="campaign-table"' in content
        assert "Draft One" in content
        assert "Sent One" not in content

    def test_list_htmx_pagination_preserves_filters(
        self, admin_client, email_type, template
    ):
        for i in range(30):
            Campaign.objects.create(
                name=f"Campaign {i}",
                email_type=email_type,
                template=template,
                subject_line=f"Subject {i}",
                status=Campaign.Status.DRAFT,
            )
        response = admin_client.get(
            "/campaigns/?q=Campaign&page=2", HTTP_HX_REQUEST="true"
        )
        assert response.status_code == 200
        content = response.content.decode()
        assert "q=Campaign" in content
        assert "page=1" in content or "page=3" in content


# ── Campaign create ────────────────────────────────────────────────────


@pytest.mark.django_db
class TestCampaignCreate:
    def test_create_get_renders_form(self, admin_client, email_type, template):
        response = admin_client.get("/campaigns/new/")
        assert response.status_code == 200
        content = response.content.decode()
        assert "New Campaign" in content
        assert "name" in content.lower()

    def test_create_post_creates_campaign(self, admin_client, email_type, template):
        data = {
            "name": "My New Campaign",
            "email_type": email_type.pk,
            "template": template.pk,
            "subject_line": "Hello World",
            "audience_status": "active",
        }
        response = admin_client.post("/campaigns/new/", data)
        assert response.status_code == 302
        campaign = Campaign.objects.get(name="My New Campaign")
        assert campaign.subject_line == "Hello World"
        assert campaign.status == Campaign.Status.DRAFT
        assert campaign.audience_filter == {"status": "active"}

    def test_create_with_scheduled_at_sets_scheduled_status(
        self, admin_client, email_type, template
    ):
        scheduled = (timezone.now() + timezone.timedelta(days=7)).strftime(
            "%Y-%m-%dT%H:%M"
        )
        data = {
            "name": "Future Campaign",
            "email_type": email_type.pk,
            "template": template.pk,
            "subject_line": "Future",
            
            "scheduled_at": scheduled,
        }
        response = admin_client.post("/campaigns/new/", data)
        assert response.status_code == 302
        campaign = Campaign.objects.get(name="Future Campaign")
        assert campaign.status == Campaign.Status.SCHEDULED
        assert campaign.scheduled_at is not None

    def test_create_with_audience_tags_builds_filter(
        self, admin_client, email_type, template
    ):
        data = {
            "name": "Filtered Campaign",
            "email_type": email_type.pk,
            "template": template.pk,
            "subject_line": "Test",
            "audience_tags": "paid, vip",
            "audience_status": "active",
        }
        response = admin_client.post("/campaigns/new/", data)
        assert response.status_code == 302
        campaign = Campaign.objects.get(name="Filtered Campaign")
        assert campaign.audience_filter == {
            "status": "active",
            "tags": ["paid", "vip"],
        }

    def test_create_empty_audience_defaults_to_active(
        self, admin_client, email_type, template
    ):
        data = {
            "name": "No Filter",
            "email_type": email_type.pk,
            "template": template.pk,
            "subject_line": "Test",
            "audience_status": "active",
        }
        response = admin_client.post("/campaigns/new/", data)
        assert response.status_code == 302
        campaign = Campaign.objects.get(name="No Filter")
        assert campaign.audience_filter == {"status": "active"}

    def test_create_missing_required_fields(self, admin_client, email_type, template):
        data = {
            "name": "",
            "email_type": email_type.pk,
            "template": template.pk,
            "subject_line": "",
        }
        response = admin_client.post("/campaigns/new/", data)
        assert response.status_code == 200
        assert Campaign.objects.count() == 0


# ── Campaign detail ────────────────────────────────────────────────────


@pytest.mark.django_db
class TestCampaignDetail:
    def test_detail_shows_campaign(self, admin_client, draft_campaign):
        response = admin_client.get(f"/campaigns/{draft_campaign.pk}/")
        assert response.status_code == 200
        content = response.content.decode()
        assert "Test Campaign" in content
        assert "Test subject" in content

    def test_detail_shows_email_type_and_template(self, admin_client, draft_campaign):
        response = admin_client.get(f"/campaigns/{draft_campaign.pk}/")
        content = response.content.decode()
        assert "Newsletter" in content
        assert "Weekly Digest" in content

    def test_detail_shows_edit_button_for_draft(self, admin_client, draft_campaign):
        response = admin_client.get(f"/campaigns/{draft_campaign.pk}/")
        content = response.content.decode()
        assert "Edit" in content
        assert f"/campaigns/{draft_campaign.pk}/edit/" in content

    def test_detail_shows_edit_button_for_scheduled(
        self, admin_client, scheduled_campaign
    ):
        response = admin_client.get(f"/campaigns/{scheduled_campaign.pk}/")
        content = response.content.decode()
        assert "Edit" in content

    def test_detail_hides_edit_button_for_sent(self, admin_client, sent_campaign):
        response = admin_client.get(f"/campaigns/{sent_campaign.pk}/")
        content = response.content.decode()
        assert f'/campaigns/{sent_campaign.pk}/edit/"' not in content

    def test_detail_shows_send_stats(self, admin_client, sent_campaign, email_type):
        sub = Subscriber.objects.create(email="test@example.com", status="active")
        EmailSend.objects.create(
            campaign=sent_campaign,
            subscriber=sub,
            email_type=email_type,
            subject_line_used="Test",
            status=EmailSend.Status.SENT,
            sent_at=timezone.now(),
        )
        EmailSend.objects.create(
            campaign=sent_campaign,
            subscriber=Subscriber.objects.create(email="test2@example.com", status="active"),
            email_type=email_type,
            subject_line_used="Test",
            status=EmailSend.Status.DELIVERED,
            delivered_at=timezone.now(),
        )
        response = admin_client.get(f"/campaigns/{sent_campaign.pk}/")
        assert response.status_code == 200
        stats = response.context["send_stats"]
        assert stats["total"] == 2
        assert stats["delivered"] == 1

    def test_detail_shows_recent_sends(self, admin_client, sent_campaign, email_type):
        sub = Subscriber.objects.create(email="test@example.com", status="active")
        EmailSend.objects.create(
            campaign=sent_campaign,
            subscriber=sub,
            email_type=email_type,
            subject_line_used="Test",
            status=EmailSend.Status.SENT,
            sent_at=timezone.now(),
        )
        response = admin_client.get(f"/campaigns/{sent_campaign.pk}/")
        content = response.content.decode()
        assert "test@example.com" in content
        assert "Recent sends" in content

    def test_detail_nonexistent_campaign(self, admin_client, db):
        response = admin_client.get("/campaigns/99999/")
        assert response.status_code == 404

    def test_detail_shows_audience_filter(self, admin_client, draft_campaign):
        draft_campaign.audience_filter = {"tags": ["newsletter"], "status": "active"}
        draft_campaign.save()
        response = admin_client.get(f"/campaigns/{draft_campaign.pk}/")
        content = response.content.decode()
        assert "newsletter" in content

    def test_detail_shows_cli_hints(self, admin_client, draft_campaign):
        response = admin_client.get(f"/campaigns/{draft_campaign.pk}/")
        content = response.content.decode()
        assert "postino campaigns get" in content
        assert "postino campaigns send-test" in content


# ── Campaign edit ──────────────────────────────────────────────────────


@pytest.mark.django_db
class TestCampaignEdit:
    def test_edit_get_renders_form(self, admin_client, draft_campaign):
        response = admin_client.get(f"/campaigns/{draft_campaign.pk}/edit/")
        assert response.status_code == 200
        content = response.content.decode()
        assert "Edit Campaign" in content
        assert draft_campaign.name in content

    def test_edit_post_updates_draft(self, admin_client, draft_campaign):
        data = {
            "name": "Updated Name",
            "email_type": draft_campaign.email_type.pk,
            "template": draft_campaign.template.pk,
            "subject_line": "Updated subject",
            "audience_tags": "vip",
            "audience_status": "active",
        }
        response = admin_client.post(f"/campaigns/{draft_campaign.pk}/edit/", data)
        assert response.status_code == 302
        draft_campaign.refresh_from_db()
        assert draft_campaign.name == "Updated Name"
        assert draft_campaign.subject_line == "Updated subject"
        assert draft_campaign.audience_filter == {"status": "active", "tags": ["vip"]}

    def test_edit_preserves_draft_status(self, admin_client, draft_campaign):
        data = {
            "name": "Still Draft",
            "email_type": draft_campaign.email_type.pk,
            "template": draft_campaign.template.pk,
            "subject_line": "Test",
            
        }
        admin_client.post(f"/campaigns/{draft_campaign.pk}/edit/", data)
        draft_campaign.refresh_from_db()
        assert draft_campaign.status == Campaign.Status.DRAFT

    def test_edit_can_schedule_draft(self, admin_client, draft_campaign):
        scheduled = (timezone.now() + timezone.timedelta(days=3)).strftime(
            "%Y-%m-%dT%H:%M"
        )
        data = {
            "name": draft_campaign.name,
            "email_type": draft_campaign.email_type.pk,
            "template": draft_campaign.template.pk,
            "subject_line": draft_campaign.subject_line,
            
            "scheduled_at": scheduled,
        }
        response = admin_client.post(f"/campaigns/{draft_campaign.pk}/edit/", data)
        assert response.status_code == 302
        draft_campaign.refresh_from_db()
        assert draft_campaign.status == Campaign.Status.SCHEDULED

    def test_edit_blocked_for_sent_campaign(self, admin_client, sent_campaign):
        response = admin_client.get(f"/campaigns/{sent_campaign.pk}/edit/")
        assert response.status_code == 302
        # Should redirect to detail with error message
        assert f"/campaigns/{sent_campaign.pk}/" in response.url

    def test_edit_blocked_for_sent_campaign_post(self, admin_client, sent_campaign):
        data = {
            "name": "Hacked Name",
            "email_type": sent_campaign.email_type.pk,
            "template": sent_campaign.template.pk,
            "subject_line": "Hacked",
            
        }
        response = admin_client.post(f"/campaigns/{sent_campaign.pk}/edit/", data)
        assert response.status_code == 302
        sent_campaign.refresh_from_db()
        assert sent_campaign.name != "Hacked Name"

    def test_edit_blocked_for_sending_campaign(
        self, admin_client, email_type, template
    ):
        campaign = Campaign.objects.create(
            name="Sending",
            email_type=email_type,
            template=template,
            subject_line="Test",
            status=Campaign.Status.SENDING,
        )
        response = admin_client.get(f"/campaigns/{campaign.pk}/edit/")
        assert response.status_code == 302

    def test_edit_scheduled_campaign(self, admin_client, scheduled_campaign):
        response = admin_client.get(f"/campaigns/{scheduled_campaign.pk}/edit/")
        assert response.status_code == 200
        content = response.content.decode()
        assert "Edit Campaign" in content

    def test_edit_nonexistent_campaign(self, admin_client, db):
        response = admin_client.get("/campaigns/99999/edit/")
        assert response.status_code == 404


# ── CampaignForm unit tests ────────────────────────────────────────────


@pytest.mark.django_db
class TestCampaignForm:
    def test_audience_fields_build_filter(self, email_type, template):
        from apps.campaigns.forms import CampaignForm

        form = CampaignForm(data={
            "name": "Test",
            "email_type": email_type.pk,
            "template": template.pk,
            "subject_line": "Test",
            "audience_tags": "paid, beta",
            "audience_status": "active",
            "health_min": 20,
            "health_max": 80,
        })
        assert form.is_valid(), form.errors
        campaign = form.save()
        assert campaign.audience_filter == {
            "status": "active",
            "tags": ["paid", "beta"],
            "health_above": 20,
            "health_below": 80,
        }

    def test_empty_audience_with_status(self, email_type, template):
        from apps.campaigns.forms import CampaignForm

        form = CampaignForm(data={
            "name": "Test",
            "email_type": email_type.pk,
            "template": template.pk,
            "subject_line": "Test",
            "audience_status": "active",
        })
        assert form.is_valid(), form.errors
        campaign = form.save()
        assert campaign.audience_filter == {"status": "active"}

    def test_empty_audience_no_status(self, email_type, template):
        from apps.campaigns.forms import CampaignForm

        form = CampaignForm(data={
            "name": "Test",
            "email_type": email_type.pk,
            "template": template.pk,
            "subject_line": "Test",
            "audience_status": "",
        })
        assert form.is_valid(), form.errors
        campaign = form.save()
        assert campaign.audience_filter == {}

    def test_naive_scheduled_at_becomes_aware(self, email_type, template):
        from apps.campaigns.forms import CampaignForm

        form = CampaignForm(data={
            "name": "Test",
            "email_type": email_type.pk,
            "template": template.pk,
            "subject_line": "Test",
            "audience_status": "active",
            "scheduled_at": "2026-12-01T10:00",
        })
        assert form.is_valid(), form.errors
        scheduled = form.cleaned_data["scheduled_at"]
        assert timezone.is_aware(scheduled)
