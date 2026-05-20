"""Tests for health score computation.

Covers:
- Individual factor scoring functions
- Composite health score calculation
- Edge cases: no activity, no email sends, various sources
- compute_all_health_scores batch operation
- Management command invocation
"""

from __future__ import annotations

from datetime import timedelta
from io import StringIO

from django.test import TestCase
from django.utils import timezone

from apps.campaigns.models import Campaign, EmailSend
from apps.consent.models import EmailType
from apps.subscribers.models import Subscriber
from apps.subscribers.services import (
    _score_email_engagement,
    _score_last_activity,
    _score_source_quality,
    _score_subscription_tenure,
    compute_all_health_scores,
    compute_subscriber_health_score,
)
from apps.templates_mgr.models import EmailTemplate


class TestScoreLastActivity(TestCase):
    def test_returns_100_when_activity_under_7_days(self) -> None:
        sub = Subscriber(email="a@test.com", status="active")
        sub.last_activity_at = timezone.now() - timedelta(days=3)
        score = _score_last_activity(sub, timezone.now())
        self.assertEqual(score, 100.0)

    def test_returns_70_when_activity_under_14_days(self) -> None:
        sub = Subscriber(email="b@test.com", status="active")
        sub.last_activity_at = timezone.now() - timedelta(days=10)
        score = _score_last_activity(sub, timezone.now())
        self.assertEqual(score, 70.0)

    def test_returns_40_when_activity_under_30_days(self) -> None:
        sub = Subscriber(email="c@test.com", status="active")
        sub.last_activity_at = timezone.now() - timedelta(days=20)
        score = _score_last_activity(sub, timezone.now())
        self.assertEqual(score, 40.0)

    def test_returns_10_when_activity_under_90_days(self) -> None:
        sub = Subscriber(email="d@test.com", status="active")
        sub.last_activity_at = timezone.now() - timedelta(days=60)
        score = _score_last_activity(sub, timezone.now())
        self.assertEqual(score, 10.0)

    def test_returns_0_when_activity_over_90_days(self) -> None:
        sub = Subscriber(email="e@test.com", status="active")
        sub.last_activity_at = timezone.now() - timedelta(days=120)
        score = _score_last_activity(sub, timezone.now())
        self.assertEqual(score, 0.0)

    def test_returns_0_when_no_activity(self) -> None:
        sub = Subscriber(email="f@test.com", status="active")
        sub.last_activity_at = None
        score = _score_last_activity(sub, timezone.now())
        self.assertEqual(score, 0.0)


class TestScoreEmailEngagement(TestCase):
    def test_returns_50_when_no_email_sends(self) -> None:
        sub = Subscriber.objects.create(email="no-sends@test.com", status="active")
        score = _score_email_engagement(sub)
        self.assertEqual(score, 50.0)

    def test_returns_high_score_when_all_opened(self) -> None:
        sub = Subscriber.objects.create(email="all-open@test.com", status="active")
        et = EmailType.objects.create(slug="test", name="Test")
        tmpl = EmailTemplate.objects.create(
            name="T", slug="t", subject_default="S", html_body="<p>X</p>"
        )
        campaign = Campaign.objects.create(
            name="C", email_type=et, template=tmpl, subject_line="S", status="sent"
        )
        for i in range(5):
            EmailSend.objects.create(
                subscriber=sub,
                campaign=campaign,
                email_type=et,
                subject_line_used="S",
                status="opened",
                sent_at=timezone.now() - timedelta(days=i),
            )
        score = _score_email_engagement(sub)
        self.assertEqual(score, 100.0)

    def test_returns_zero_when_none_opened(self) -> None:
        sub = Subscriber.objects.create(email="none-open@test.com", status="active")
        et = EmailType.objects.create(slug="test2", name="Test2")
        tmpl = EmailTemplate.objects.create(
            name="T2", slug="t2", subject_default="S", html_body="<p>X</p>"
        )
        campaign = Campaign.objects.create(
            name="C2", email_type=et, template=tmpl, subject_line="S", status="sent"
        )
        for i in range(5):
            EmailSend.objects.create(
                subscriber=sub,
                campaign=campaign,
                email_type=et,
                subject_line_used="S",
                status="delivered",
                sent_at=timezone.now() - timedelta(days=i),
            )
        score = _score_email_engagement(sub)
        self.assertEqual(score, 0.0)


class TestScoreSubscriptionTenure(TestCase):
    def test_returns_100_when_over_6_months(self) -> None:
        sub = Subscriber.objects.create(email="old@test.com", status="active")
        sub.created_at = timezone.now() - timedelta(days=200)
        score = _score_subscription_tenure(sub, timezone.now())
        self.assertEqual(score, 100.0)

    def test_returns_70_when_over_3_months(self) -> None:
        sub = Subscriber.objects.create(email="mid@test.com", status="active")
        sub.created_at = timezone.now() - timedelta(days=100)
        score = _score_subscription_tenure(sub, timezone.now())
        self.assertEqual(score, 70.0)

    def test_returns_40_when_over_1_month(self) -> None:
        sub = Subscriber.objects.create(email="month@test.com", status="active")
        sub.created_at = timezone.now() - timedelta(days=45)
        score = _score_subscription_tenure(sub, timezone.now())
        self.assertEqual(score, 40.0)

    def test_returns_20_when_under_1_month(self) -> None:
        sub = Subscriber.objects.create(email="new@test.com", status="active")
        sub.created_at = timezone.now() - timedelta(days=10)
        score = _score_subscription_tenure(sub, timezone.now())
        self.assertEqual(score, 20.0)


class TestScoreSourceQuality(TestCase):
    def test_returns_100_for_double_optin(self) -> None:
        sub = Subscriber(
            email="doi@test.com",
            status="active",
            source="manual",
            double_optin_confirmed_at=timezone.now(),
        )
        self.assertEqual(_score_source_quality(sub), 100.0)

    def test_returns_80_for_signup_form(self) -> None:
        sub = Subscriber(email="sf@test.com", status="active", source="signup_form")
        self.assertEqual(_score_source_quality(sub), 80.0)

    def test_returns_50_for_import(self) -> None:
        sub = Subscriber(email="imp@test.com", status="active", source="import")
        self.assertEqual(_score_source_quality(sub), 50.0)

    def test_returns_50_for_sync(self) -> None:
        sub = Subscriber(email="sync@test.com", status="active", source="sync")
        self.assertEqual(_score_source_quality(sub), 50.0)


class TestComputeSubscriberHealthScore(TestCase):
    def test_score_is_between_0_and_100(self) -> None:
        sub = Subscriber.objects.create(email="range@test.com", status="active")
        score = compute_subscriber_health_score(sub)
        self.assertGreaterEqual(score, 0)
        self.assertLessEqual(score, 100)

    def test_high_score_for_engaged_subscriber(self) -> None:
        sub = Subscriber.objects.create(
            email="engaged@test.com",
            status="active",
            last_activity_at=timezone.now() - timedelta(days=2),
            double_optin_confirmed_at=timezone.now(),
            source="manual",
        )
        score = compute_subscriber_health_score(sub)
        self.assertGreaterEqual(score, 60)

    def test_low_score_for_inactive_subscriber(self) -> None:
        sub = Subscriber.objects.create(
            email="inactive@test.com",
            status="active",
            source="import",
        )
        sub.last_activity_at = None
        score = compute_subscriber_health_score(sub)
        self.assertLess(score, 40)


class TestComputeAllHealthScores(TestCase):
    def test_updates_active_subscribers(self) -> None:
        sub1 = Subscriber.objects.create(
            email="a@all.test", status="active", health_score=50
        )
        sub2 = Subscriber.objects.create(
            email="b@all.test", status="active", health_score=50
        )

        result = compute_all_health_scores()

        sub1.refresh_from_db()
        sub2.refresh_from_db()
        self.assertEqual(result.total, 2)
        self.assertGreaterEqual(result.updated, 0)
        self.assertIn("healthy", result.distribution)
        self.assertIn("at_risk", result.distribution)
        self.assertIn("critical", result.distribution)
        total_dist = (
            result.distribution["healthy"]
            + result.distribution["at_risk"]
            + result.distribution["critical"]
        )
        self.assertEqual(total_dist, 2)

    def test_skips_suppressed_subscribers(self) -> None:
        Subscriber.objects.create(email="sup@all.test", status="unsubscribed")
        result = compute_all_health_scores()
        self.assertEqual(result.total, 0)


class TestComputeHealthScoresCommand(TestCase):
    def test_command_runs_successfully(self) -> None:
        from django.core.management import call_command

        out = StringIO()
        call_command("compute_health_scores", stdout=out)
        output = out.getvalue()
        self.assertIn("Computing health scores", output)
        self.assertIn("Processed", output)
