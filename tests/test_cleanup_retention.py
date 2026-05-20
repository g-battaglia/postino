"""Tests for the cleanup_retention management command.

CRITICAL INVARIANTS verified:
- ConsentRecord rows are NEVER deleted
- UnsubscribeEvent rows are NEVER deleted
- EmailSend records ARE deleted past retention
- Suppressed subscriber personal fields ARE blanked past retention
"""

from __future__ import annotations

from datetime import timedelta
from io import StringIO

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from apps.campaigns.models import Campaign, EmailSend
from apps.consent.models import ConsentRecord, EmailType, UnsubscribeEvent
from apps.subscribers.models import Subscriber
from apps.templates_mgr.models import EmailTemplate


class TestCleanupRetentionCommand(TestCase):
    def setUp(self) -> None:
        self.et = EmailType.objects.create(slug="test", name="Test")
        self.tmpl = EmailTemplate.objects.create(
            name="T", slug="t", subject_default="S", html_body="<p>X</p>"
        )
        self.campaign = Campaign.objects.create(
            name="C", email_type=self.et, template=self.tmpl,
            subject_line="S", status="sent",
        )

    def test_deletes_old_email_sends(self) -> None:
        sub = Subscriber.objects.create(email="old@test.com", status="active")
        EmailSend.objects.create(
            subscriber=sub, campaign=self.campaign, email_type=self.et,
            subject_line_used="S", status="sent",
            sent_at=timezone.now() - timedelta(days=800),
        )
        out = StringIO()
        call_command("cleanup_retention", stdout=out)
        self.assertEqual(EmailSend.objects.count(), 0)

    def test_preserves_recent_email_sends(self) -> None:
        sub = Subscriber.objects.create(email="recent@test.com", status="active")
        EmailSend.objects.create(
            subscriber=sub, campaign=self.campaign, email_type=self.et,
            subject_line_used="S", status="sent",
            sent_at=timezone.now() - timedelta(days=10),
        )
        out = StringIO()
        call_command("cleanup_retention", stdout=out)
        self.assertEqual(EmailSend.objects.count(), 1)

    def test_never_deletes_consent_records(self) -> None:
        sub = Subscriber.objects.create(email="consent@test.com", status="unsubscribed")
        ConsentRecord.objects.create(
            subscriber=sub, email_type=self.et,
            action="grant", method="test",
        )
        out = StringIO()
        call_command("cleanup_retention", stdout=out)
        self.assertEqual(ConsentRecord.objects.count(), 1)

    def test_never_deletes_unsubscribe_events(self) -> None:
        sub = Subscriber.objects.create(email="event@test.com", status="unsubscribed")
        UnsubscribeEvent.objects.create(
            subscriber=sub, email=sub.email, method="test",
        )
        out = StringIO()
        call_command("cleanup_retention", stdout=out)
        self.assertEqual(UnsubscribeEvent.objects.count(), 1)

    def test_blanks_suppressed_subscriber_data_past_retention(self) -> None:
        sub = Subscriber.objects.create(
            email="purge@test.com",
            status="unsubscribed",
            name="John",
            metadata={"plan": "pro"},
            source_id="ext-123",
            ip_address="1.2.3.4",
        )
        Subscriber.objects.filter(pk=sub.pk).update(
            updated_at=timezone.now() - timedelta(days=100)
        )

        out = StringIO()
        call_command("cleanup_retention", stdout=out)

        sub.refresh_from_db()
        self.assertEqual(sub.name, "")
        self.assertEqual(sub.metadata, {})
        self.assertEqual(sub.source_id, "")
        self.assertIsNone(sub.ip_address)

    def test_blanks_stale_double_optin_fields_when_other_fields_blank(self) -> None:
        sub = Subscriber.objects.create(
            email="token@test.com",
            status="unsubscribed",
            double_optin_token="stale-token",
            double_optin_confirmed_at=timezone.now() - timedelta(days=120),
        )
        Subscriber.objects.filter(pk=sub.pk).update(
            updated_at=timezone.now() - timedelta(days=100)
        )

        out = StringIO()
        call_command("cleanup_retention", stdout=out)

        sub.refresh_from_db()
        self.assertIsNone(sub.double_optin_token)
        self.assertIsNone(sub.double_optin_confirmed_at)

    def test_does_not_purge_recent_suppressed_subscribers(self) -> None:
        sub = Subscriber.objects.create(
            email="keep@test.com",
            status="unsubscribed",
            name="Jane",
            metadata={"plan": "basic"},
        )

        out = StringIO()
        call_command("cleanup_retention", stdout=out)

        sub.refresh_from_db()
        self.assertEqual(sub.name, "Jane")

    def test_does_not_purge_active_subscribers(self) -> None:
        sub = Subscriber.objects.create(
            email="active@test.com",
            status="active",
            name="Active User",
            metadata={"plan": "pro"},
        )
        Subscriber.objects.filter(pk=sub.pk).update(
            updated_at=timezone.now() - timedelta(days=400)
        )

        out = StringIO()
        call_command("cleanup_retention", stdout=out)

        sub.refresh_from_db()
        self.assertEqual(sub.name, "Active User")

    def test_dry_run_does_not_modify_data(self) -> None:
        sub = Subscriber.objects.create(
            email="dry@test.com",
            status="unsubscribed",
            name="Dry Run",
        )
        Subscriber.objects.filter(pk=sub.pk).update(
            updated_at=timezone.now() - timedelta(days=100)
        )

        EmailSend.objects.create(
            subscriber=sub, campaign=self.campaign, email_type=self.et,
            subject_line_used="S", status="sent",
            sent_at=timezone.now() - timedelta(days=800),
        )

        out = StringIO()
        call_command("cleanup_retention", "--dry-run", stdout=out)

        sub.refresh_from_db()
        self.assertEqual(sub.name, "Dry Run")
        self.assertEqual(EmailSend.objects.count(), 1)
        self.assertIn("DRY RUN", out.getvalue())
