"""Tests for postino CLI: campaigns list, get, create, send, send-test.

Also covers the send_test_email service directly to verify unsubscribe
headers and visible unsubscribe link in test emails.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from apps.campaigns.models import Campaign
from apps.campaigns.services import TestEmailError, send_test_email
from apps.consent.models import ConsentRecord, EmailType
from apps.subscribers.models import Subscriber
from apps.templates_mgr.models import EmailTemplate
from cli.cli import main

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeBackend:
    """In-memory fake backend that records every call."""

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
def email_type(db: None) -> EmailType:
    return EmailType.objects.create(
        slug="newsletter",
        name="Newsletter",
        is_transactional=False,
    )


@pytest.fixture
def template() -> EmailTemplate:
    return EmailTemplate.objects.create(
        name="Test Template",
        slug="test-template",
        subject_default="Hello {{ subscriber_name }}",
        html_body="<p>Hi {{ subscriber_name }}</p>",
        text_body="Hi {{ subscriber_name }}",
    )


def _make_campaign(
    email_type: EmailType,
    template: EmailTemplate,
    **kwargs,
) -> Campaign:
    defaults = {
        "name": "Test Campaign",
        "subject_line": "Test Subject",
        "status": Campaign.Status.DRAFT,
        "audience_filter": {},
    }
    defaults.update(kwargs)
    return Campaign.objects.create(email_type=email_type, template=template, **defaults)


def _make_subscriber(email: str) -> Subscriber:
    return Subscriber.objects.create(email=email, status=Subscriber.Status.ACTIVE)


def _grant_consent(subscriber: Subscriber, email_type: EmailType) -> None:
    ConsentRecord.objects.create(
        subscriber=subscriber,
        email_type=email_type,
        action=ConsentRecord.Action.GRANT,
        method="test",
    )


# ---------------------------------------------------------------------------
# CLI: campaigns list
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestCampaignsList:
    def test_campaigns_list_empty(self) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["campaigns", "list"])
        assert result.exit_code == 0
        assert "No campaigns found" in result.output

    def test_campaigns_list_with_data(self, email_type: EmailType, template: EmailTemplate) -> None:
        _make_campaign(email_type, template, name="Camp A")
        _make_campaign(email_type, template, name="Camp B")

        runner = CliRunner()
        result = runner.invoke(main, ["campaigns", "list"])
        assert result.exit_code == 0
        assert "Camp A" in result.output
        assert "Camp B" in result.output

    def test_campaigns_list_json(self, email_type: EmailType, template: EmailTemplate) -> None:
        _make_campaign(email_type, template, name="Camp A")

        runner = CliRunner()
        result = runner.invoke(main, ["campaigns", "list", "--json"])
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert parsed["ok"] is True
        assert parsed["data"]["count"] == 1
        assert parsed["data"]["campaigns"][0]["name"] == "Camp A"

    def test_campaigns_list_status_filter(
        self, email_type: EmailType, template: EmailTemplate,
    ) -> None:
        _make_campaign(email_type, template, name="Draft Camp", status=Campaign.Status.DRAFT)
        _make_campaign(
            email_type, template, name="Sent Camp", status=Campaign.Status.SENT,
        )

        runner = CliRunner()
        result = runner.invoke(main, ["campaigns", "list", "--status", "sent"])
        assert result.exit_code == 0
        assert "Sent Camp" in result.output
        assert "Draft Camp" not in result.output


# ---------------------------------------------------------------------------
# CLI: campaigns get
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestCampaignsGet:
    def test_campaigns_get_by_id(self, email_type: EmailType, template: EmailTemplate) -> None:
        campaign = _make_campaign(email_type, template, name="My Camp")

        runner = CliRunner()
        result = runner.invoke(main, ["campaigns", "get", str(campaign.pk)])
        assert result.exit_code == 0
        assert "My Camp" in result.output
        assert "Test Subject" in result.output

    def test_campaigns_get_not_found(self) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["campaigns", "get", "99999"])
        assert result.exit_code == 1
        assert "not found" in result.output.lower()

    def test_campaigns_get_not_found_json(self) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["campaigns", "get", "99999", "--json"])
        assert result.exit_code == 1
        parsed = json.loads(result.output)
        assert parsed["ok"] is False
        assert "not found" in parsed["error"].lower()

    def test_campaigns_get_json(self, email_type: EmailType, template: EmailTemplate) -> None:
        campaign = _make_campaign(email_type, template, name="JSON Camp")

        runner = CliRunner()
        result = runner.invoke(main, ["campaigns", "get", str(campaign.pk), "--json"])
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert parsed["ok"] is True
        assert parsed["data"]["name"] == "JSON Camp"
        assert parsed["data"]["email_type"] == "newsletter"
        assert parsed["data"]["template"] == "test-template"


# ---------------------------------------------------------------------------
# CLI: campaigns create
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestCampaignsCreate:
    def test_campaigns_create_basic(self, email_type: EmailType, template: EmailTemplate) -> None:
        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                "campaigns", "create",
                "--name", "New Campaign",
                "--email-type", "newsletter",
                "--template", "test-template",
                "--subject", "Hello World",
            ],
        )
        assert result.exit_code == 0
        assert "Created campaign" in result.output
        assert "New Campaign" in result.output
        assert Campaign.objects.filter(name="New Campaign").exists()

    def test_campaigns_create_json(self, email_type: EmailType, template: EmailTemplate) -> None:
        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                "campaigns", "create",
                "--name", "JSON Camp",
                "--email-type", "newsletter",
                "--template", "test-template",
                "--subject", "Subj",
                "--json",
            ],
        )
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert parsed["ok"] is True
        assert parsed["data"]["name"] == "JSON Camp"
        assert parsed["data"]["status"] == "draft"

    def test_campaigns_create_with_audience_filter(
        self, email_type: EmailType, template: EmailTemplate,
    ) -> None:
        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                "campaigns", "create",
                "--name", "Filtered",
                "--email-type", "newsletter",
                "--template", "test-template",
                "--subject", "Subj",
                "--audience-filter", '{"tags": ["vip"]}',
            ],
        )
        assert result.exit_code == 0
        campaign = Campaign.objects.get(name="Filtered")
        assert campaign.audience_filter == {"tags": ["vip"]}

    def test_campaigns_create_with_scheduled_at(
        self, email_type: EmailType, template: EmailTemplate,
    ) -> None:
        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                "campaigns", "create",
                "--name", "Scheduled",
                "--email-type", "newsletter",
                "--template", "test-template",
                "--subject", "Subj",
                "--scheduled-at", "2026-06-01T10:00:00+00:00",
            ],
        )
        assert result.exit_code == 0
        campaign = Campaign.objects.get(name="Scheduled")
        assert campaign.status == Campaign.Status.SCHEDULED
        assert campaign.scheduled_at is not None

    def test_campaigns_create_invalid_email_type(
        self, email_type: EmailType, template: EmailTemplate,
    ) -> None:
        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                "campaigns", "create",
                "--name", "Bad",
                "--email-type", "nonexistent",
                "--template", "test-template",
                "--subject", "Subj",
            ],
        )
        assert result.exit_code == 1
        assert "not found" in result.output.lower()

    def test_campaigns_create_invalid_email_type_json(
        self, email_type: EmailType, template: EmailTemplate,
    ) -> None:
        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                "campaigns", "create",
                "--name", "Bad",
                "--email-type", "nonexistent",
                "--template", "test-template",
                "--subject", "Subj",
                "--json",
            ],
        )
        assert result.exit_code == 1
        parsed = json.loads(result.output)
        assert parsed["ok"] is False
        assert "not found" in parsed["error"].lower()

    def test_campaigns_create_invalid_template(
        self, email_type: EmailType, template: EmailTemplate,
    ) -> None:
        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                "campaigns", "create",
                "--name", "Bad",
                "--email-type", "newsletter",
                "--template", "nonexistent",
                "--subject", "Subj",
            ],
        )
        assert result.exit_code == 1
        assert "not found" in result.output.lower()

    def test_campaigns_create_invalid_audience_filter_json(
        self, email_type: EmailType, template: EmailTemplate,
    ) -> None:
        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                "campaigns", "create",
                "--name", "Bad Filter",
                "--email-type", "newsletter",
                "--template", "test-template",
                "--subject", "Subj",
                "--audience-filter", "not-json",
            ],
        )
        assert result.exit_code == 1
        assert "invalid" in result.output.lower() or "json" in result.output.lower()

    def test_campaigns_create_invalid_audience_filter_json_flag(
        self, email_type: EmailType, template: EmailTemplate,
    ) -> None:
        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                "campaigns", "create",
                "--name", "Bad Filter",
                "--email-type", "newsletter",
                "--template", "test-template",
                "--subject", "Subj",
                "--audience-filter", "{bad",
                "--json",
            ],
        )
        assert result.exit_code == 1
        parsed = json.loads(result.output)
        assert parsed["ok"] is False

    def test_campaigns_create_invalid_scheduled_at(
        self, email_type: EmailType, template: EmailTemplate,
    ) -> None:
        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                "campaigns", "create",
                "--name", "Bad Date",
                "--email-type", "newsletter",
                "--template", "test-template",
                "--subject", "Subj",
                "--scheduled-at", "not-a-date",
            ],
        )
        assert result.exit_code == 1

    def test_campaigns_create_invalid_scheduled_at_json(
        self, email_type: EmailType, template: EmailTemplate,
    ) -> None:
        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                "campaigns", "create",
                "--name", "Bad Date",
                "--email-type", "newsletter",
                "--template", "test-template",
                "--subject", "Subj",
                "--scheduled-at", "not-a-date",
                "--json",
            ],
        )
        assert result.exit_code == 1
        parsed = json.loads(result.output)
        assert parsed["ok"] is False


# ---------------------------------------------------------------------------
# CLI: campaigns send
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestCampaignsSend:
    def test_campaigns_send(
        self, fake_backend: _FakeBackend, email_type: EmailType, template: EmailTemplate,
    ) -> None:
        sub = _make_subscriber("test@example.com")
        _grant_consent(sub, email_type)
        campaign = _make_campaign(email_type, template)

        runner = CliRunner()
        result = runner.invoke(main, ["campaigns", "send", str(campaign.pk)])
        assert result.exit_code == 0
        assert "sent" in result.output.lower()
        assert "Sent:     1" in result.output

    def test_campaigns_send_json(
        self, fake_backend: _FakeBackend, email_type: EmailType, template: EmailTemplate,
    ) -> None:
        sub = _make_subscriber("test@example.com")
        _grant_consent(sub, email_type)
        campaign = _make_campaign(email_type, template)

        runner = CliRunner()
        result = runner.invoke(main, ["campaigns", "send", str(campaign.pk), "--json"])
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert parsed["ok"] is True
        assert parsed["data"]["sent"] == 1
        assert parsed["data"]["campaign_name"] == "Test Campaign"

    def test_campaigns_send_nonexistent(self) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["campaigns", "send", "99999"])
        assert result.exit_code == 1
        assert "not found" in result.output.lower()

    def test_campaigns_send_nonexistent_json(self) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["campaigns", "send", "99999", "--json"])
        assert result.exit_code == 1
        parsed = json.loads(result.output)
        assert parsed["ok"] is False

    def test_campaigns_send_already_sent(
        self, fake_backend: _FakeBackend, email_type: EmailType, template: EmailTemplate,
    ) -> None:
        campaign = _make_campaign(email_type, template, status=Campaign.Status.SENT)
        runner = CliRunner()
        result = runner.invoke(main, ["campaigns", "send", str(campaign.pk)])
        assert result.exit_code == 1
        assert "cannot send" in result.output.lower()


# ---------------------------------------------------------------------------
# CLI: campaigns send-test
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestCampaignsSendTest:
    def test_campaigns_send_test(
        self, fake_backend: _FakeBackend, email_type: EmailType, template: EmailTemplate,
    ) -> None:
        campaign = _make_campaign(email_type, template)

        runner = CliRunner()
        result = runner.invoke(
            main, ["campaigns", "send-test", str(campaign.pk), "admin@example.com"],
        )
        assert result.exit_code == 0
        assert "Test email sent to admin@example.com" in result.output

    def test_campaigns_send_test_json(
        self, fake_backend: _FakeBackend, email_type: EmailType, template: EmailTemplate,
    ) -> None:
        campaign = _make_campaign(email_type, template)

        runner = CliRunner()
        result = runner.invoke(
            main, ["campaigns", "send-test", str(campaign.pk), "admin@example.com", "--json"],
        )
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert parsed["ok"] is True
        assert parsed["data"]["recipient"] == "admin@example.com"
        assert parsed["data"]["subject"] == "Test Subject"

    def test_campaigns_send_test_nonexistent_campaign(self) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["campaigns", "send-test", "99999", "x@example.com"])
        assert result.exit_code == 1
        assert "not found" in result.output.lower()

    def test_campaigns_send_test_nonexistent_campaign_json(self) -> None:
        runner = CliRunner()
        result = runner.invoke(
            main, ["campaigns", "send-test", "99999", "x@example.com", "--json"],
        )
        assert result.exit_code == 1
        parsed = json.loads(result.output)
        assert parsed["ok"] is False


# ---------------------------------------------------------------------------
# Service: send_test_email
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestSendTestEmailService:
    def test_send_test_email_includes_unsubscribe_headers(
        self, fake_backend: _FakeBackend, email_type: EmailType, template: EmailTemplate,
    ) -> None:
        campaign = _make_campaign(email_type, template)

        send_test_email(campaign.pk, "tester@example.com")

        assert len(fake_backend.calls) == 1
        headers = fake_backend.calls[0]["headers"]
        assert "List-Unsubscribe" in headers
        assert "/unsubscribe/one-click/" in headers["List-Unsubscribe"]
        assert headers["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"

    def test_send_test_email_visible_unsubscribe_url_in_body(
        self, fake_backend: _FakeBackend, email_type: EmailType, template: EmailTemplate,
    ) -> None:
        campaign = _make_campaign(email_type, template)

        send_test_email(campaign.pk, "tester@example.com")

        html = fake_backend.calls[0]["html"]
        text = fake_backend.calls[0]["text"]
        assert "/unsubscribe/" in html
        assert "/unsubscribe/" in text
        assert "/unsubscribe/one-click/" not in html
        assert "/unsubscribe/one-click/" not in text

    def test_send_test_email_renders_subject(
        self, fake_backend: _FakeBackend, email_type: EmailType, template: EmailTemplate,
    ) -> None:
        campaign = _make_campaign(email_type, template, subject_line="Custom Subject")

        result = send_test_email(campaign.pk, "tester@example.com")

        assert result.subject == "Custom Subject"
        assert fake_backend.calls[0]["subject"] == "Custom Subject"

    def test_send_test_email_nonexistent_campaign_raises(
        self, fake_backend: _FakeBackend,
    ) -> None:
        with pytest.raises(TestEmailError, match="not found"):
            send_test_email(99999, "x@example.com")

    def test_send_test_email_returns_provider_id(
        self, fake_backend: _FakeBackend, email_type: EmailType, template: EmailTemplate,
    ) -> None:
        campaign = _make_campaign(email_type, template)

        result = send_test_email(campaign.pk, "tester@example.com")

        assert result.provider_message_id is not None
        assert result.provider_message_id.startswith("fake-")

    def test_send_test_email_no_production_email_send_created(
        self, fake_backend: _FakeBackend, email_type: EmailType, template: EmailTemplate,
    ) -> None:
        from apps.campaigns.models import EmailSend

        campaign = _make_campaign(email_type, template)

        send_test_email(campaign.pk, "tester@example.com")

        assert EmailSend.objects.count() == 0

    def test_send_test_email_does_not_change_campaign_status(
        self, fake_backend: _FakeBackend, email_type: EmailType, template: EmailTemplate,
    ) -> None:
        campaign = _make_campaign(email_type, template)

        send_test_email(campaign.pk, "tester@example.com")

        campaign.refresh_from_db()
        assert campaign.status == Campaign.Status.DRAFT


# ---------------------------------------------------------------------------
# Hardening: audience_filter type validation
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestCampaignsCreateAudienceFilterValidation:
    def test_audience_filter_array_rejected(
        self, email_type: EmailType, template: EmailTemplate,
    ) -> None:
        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                "campaigns", "create",
                "--name", "Bad Filter",
                "--email-type", "newsletter",
                "--template", "test-template",
                "--subject", "Subj",
                "--audience-filter", '["bad"]',
            ],
        )
        assert result.exit_code == 1
        assert "json object" in result.output.lower()
        assert not Campaign.objects.exists()

    def test_audience_filter_array_rejected_json(
        self, email_type: EmailType, template: EmailTemplate,
    ) -> None:
        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                "campaigns", "create",
                "--name", "Bad Filter",
                "--email-type", "newsletter",
                "--template", "test-template",
                "--subject", "Subj",
                "--audience-filter", '["bad"]',
                "--json",
            ],
        )
        assert result.exit_code == 1
        parsed = json.loads(result.output)
        assert parsed["ok"] is False
        assert "json object" in parsed["error"].lower()
        assert not Campaign.objects.exists()

    def test_audience_filter_string_rejected(
        self, email_type: EmailType, template: EmailTemplate,
    ) -> None:
        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                "campaigns", "create",
                "--name", "Bad Filter",
                "--email-type", "newsletter",
                "--template", "test-template",
                "--subject", "Subj",
                "--audience-filter", '"just-a-string"',
            ],
        )
        assert result.exit_code == 1
        assert "json object" in result.output.lower()
        assert not Campaign.objects.exists()


# ---------------------------------------------------------------------------
# Hardening: send-test catches backend/render failures
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestCampaignsSendTestBackendFailure:
    def test_send_test_backend_failure_json_envelope(
        self, email_type: EmailType, template: EmailTemplate,
    ) -> None:
        failing_backend = _FakeBackend()
        failing_backend.send = lambda *a, **kw: (_ for _ in ()).throw(
            RuntimeError("Provider timeout"),
        )

        campaign = _make_campaign(email_type, template)

        with patch("apps.campaigns.services.get_backend", return_value=failing_backend):
            runner = CliRunner()
            result = runner.invoke(
                main,
                ["campaigns", "send-test", str(campaign.pk), "admin@example.com", "--json"],
            )

        assert result.exit_code == 1
        parsed = json.loads(result.output)
        assert parsed["ok"] is False
        assert "Provider timeout" in parsed["error"]

    def test_send_test_backend_failure_plain(
        self, email_type: EmailType, template: EmailTemplate,
    ) -> None:
        failing_backend = _FakeBackend()
        failing_backend.send = lambda *a, **kw: (_ for _ in ()).throw(
            RuntimeError("Provider timeout"),
        )

        campaign = _make_campaign(email_type, template)

        with patch("apps.campaigns.services.get_backend", return_value=failing_backend):
            runner = CliRunner()
            result = runner.invoke(
                main,
                ["campaigns", "send-test", str(campaign.pk), "admin@example.com"],
            )

        assert result.exit_code == 1
        assert "Provider timeout" in result.output


# ---------------------------------------------------------------------------
# Hardening: scheduled_at naive datetime becomes aware
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestCampaignsCreateScheduledAtTimezone:
    def test_naive_scheduled_at_stored_aware(
        self, email_type: EmailType, template: EmailTemplate,
    ) -> None:
        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                "campaigns", "create",
                "--name", "Naive Date",
                "--email-type", "newsletter",
                "--template", "test-template",
                "--subject", "Subj",
                "--scheduled-at", "2026-06-15T10:00:00",
            ],
        )
        assert result.exit_code == 0
        campaign = Campaign.objects.get(name="Naive Date")
        assert campaign.scheduled_at is not None
        assert campaign.scheduled_at.tzinfo is not None

    def test_naive_scheduled_at_json_stored_aware(
        self, email_type: EmailType, template: EmailTemplate,
    ) -> None:
        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                "campaigns", "create",
                "--name", "Naive JSON",
                "--email-type", "newsletter",
                "--template", "test-template",
                "--subject", "Subj",
                "--scheduled-at", "2026-07-01T08:30:00",
                "--json",
            ],
        )
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert parsed["ok"] is True
        campaign = Campaign.objects.get(name="Naive JSON")
        assert campaign.scheduled_at is not None
        assert campaign.scheduled_at.tzinfo is not None
