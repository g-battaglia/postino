"""Tests for analytics services, CLI, and views.

Covers:
- Overview metrics computation
- Health distribution
- Churn metrics
- Campaign stats
- Sequence performance
- Growth data
- Analytics views render correctly
- CLI analytics commands produce correct output
"""

from __future__ import annotations

import json
from datetime import timedelta

from click.testing import CliRunner
from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.utils import timezone

from apps.analytics.services import (
    get_campaign_stats,
    get_churn_metrics,
    get_growth_data,
    get_health_distribution,
    get_overview_metrics,
    get_recent_campaigns_with_stats,
    get_sequence_performance,
)
from apps.campaigns.models import (
    Campaign,
    EmailSend,
    Sequence,
    SequenceEnrollment,
    SequenceStep,
)
from apps.consent.models import EmailType
from apps.subscribers.models import Subscriber
from apps.templates_mgr.models import EmailTemplate
from cli.cli import main


def _create_test_data() -> None:
    """Create a minimal set of test objects for analytics tests."""
    et = EmailType.objects.create(slug="digest", name="Digest")
    tmpl = EmailTemplate.objects.create(
        name="Weekly", slug="weekly", subject_default="Your digest", html_body="<p>Hi</p>"
    )
    return et, tmpl


class TestOverviewMetrics(TestCase):
    def test_empty_database(self) -> None:
        metrics = get_overview_metrics(days=30)
        self.assertEqual(metrics.total_subscribers, 0)
        self.assertEqual(metrics.active_subscribers, 0)
        self.assertEqual(metrics.emails_sent, 0)
        self.assertEqual(metrics.churn_rate, 0.0)

    def test_with_subscribers_and_sends(self) -> None:
        et, tmpl = _create_test_data()
        sub = Subscriber.objects.create(email="test@overview.com", status="active")
        campaign = Campaign.objects.create(
            name="Test", email_type=et, template=tmpl, subject_line="Hi", status="sent"
        )
        EmailSend.objects.create(
            subscriber=sub,
            campaign=campaign,
            email_type=et,
            subject_line_used="Hi",
            status="opened",
            sent_at=timezone.now() - timedelta(days=1),
        )

        metrics = get_overview_metrics(days=30)
        self.assertEqual(metrics.active_subscribers, 1)
        self.assertEqual(metrics.emails_sent, 1)
        self.assertEqual(metrics.emails_opened, 1)
        self.assertEqual(metrics.open_rate, 100.0)


class TestHealthDistribution(TestCase):
    def test_empty_database(self) -> None:
        dist = get_health_distribution()
        self.assertEqual(dist.total, 0)
        self.assertEqual(dist.healthy_pct, 0.0)

    def test_with_subscribers(self) -> None:
        Subscriber.objects.create(email="h@dist.com", status="active", health_score=80)
        Subscriber.objects.create(email="r@dist.com", status="active", health_score=50)
        Subscriber.objects.create(email="c@dist.com", status="active", health_score=20)

        dist = get_health_distribution()
        self.assertEqual(dist.total, 3)
        self.assertEqual(dist.healthy_count, 1)
        self.assertEqual(dist.at_risk_count, 1)
        self.assertEqual(dist.critical_count, 1)


class TestChurnMetrics(TestCase):
    def test_no_churn(self) -> None:
        Subscriber.objects.create(email="stable@churn.com", status="active")
        churn = get_churn_metrics(days=30)
        self.assertEqual(churn.churned_in_period, 0)
        self.assertEqual(churn.churn_rate, 0.0)

    def test_with_churned_subscribers(self) -> None:
        Subscriber.objects.create(email="gone@churn.com", status="unsubscribed")
        churn = get_churn_metrics(days=30)
        self.assertGreaterEqual(churn.churned_in_period, 1)
        self.assertIn("unsubscribed", churn.churned_by_reason)


class TestCampaignStats(TestCase):
    def test_nonexistent_campaign(self) -> None:
        result = get_campaign_stats(99999)
        self.assertIsNone(result)

    def test_existing_campaign(self) -> None:
        et, tmpl = _create_test_data()
        campaign = Campaign.objects.create(
            name="Stats Test", email_type=et, template=tmpl, subject_line="S", status="sent"
        )
        sub = Subscriber.objects.create(email="cs@test.com", status="active")
        EmailSend.objects.create(
            subscriber=sub,
            campaign=campaign,
            email_type=et,
            subject_line_used="S",
            status="clicked",
            sent_at=timezone.now(),
        )

        stats = get_campaign_stats(campaign.pk)
        self.assertIsNotNone(stats)
        self.assertEqual(stats.sent_count, 1)
        self.assertEqual(stats.opened_count, 1)
        self.assertEqual(stats.clicked_count, 1)


class TestSequencePerformance(TestCase):
    def test_nonexistent_sequence(self) -> None:
        result = get_sequence_performance(99999)
        self.assertIsNone(result)

    def test_existing_sequence(self) -> None:
        et, tmpl = _create_test_data()
        seq = Sequence.objects.create(
            name="Welcome", slug="welcome", trigger_type="manual"
        )
        SequenceStep.objects.create(
            sequence=seq, order=1, delay_hours=0, email_type=et, template=tmpl
        )
        sub = Subscriber.objects.create(email="sp@test.com", status="active")
        SequenceEnrollment.objects.create(subscriber=sub, sequence=seq, status="completed")

        perf = get_sequence_performance(seq.pk)
        self.assertIsNotNone(perf)
        self.assertEqual(perf.total_enrollments, 1)
        self.assertEqual(perf.completed_enrollments, 1)
        self.assertEqual(perf.completion_rate, 100.0)


class TestGrowthData(TestCase):
    def test_returns_six_months(self) -> None:
        data = get_growth_data(months=6)
        self.assertEqual(len(data), 6)
        self.assertIn("month", data[0])
        self.assertIn("new", data[0])
        self.assertIn("churned", data[0])


class TestRecentCampaignsWithStats(TestCase):
    def test_returns_campaigns(self) -> None:
        et, tmpl = _create_test_data()
        Campaign.objects.create(
            name="Recent", email_type=et, template=tmpl, subject_line="R", status="sent"
        )
        results = get_recent_campaigns_with_stats(limit=5)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["name"], "Recent")


class TestAnalyticsViews(TestCase):
    def setUp(self) -> None:
        self.client = Client()
        self.user = User.objects.create_superuser("admin", "admin@test.com", "password")

    def test_analytics_index_requires_login(self) -> None:
        response = self.client.get("/analytics/")
        self.assertEqual(response.status_code, 302)

    def test_analytics_index_renders(self) -> None:
        self.client.login(username="admin", password="password")
        response = self.client.get("/analytics/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Analytics")

    def test_analytics_index_with_days_param(self) -> None:
        self.client.login(username="admin", password="password")
        response = self.client.get("/analytics/?days=7")
        self.assertEqual(response.status_code, 200)

    def test_analytics_index_invalid_days_uses_default(self) -> None:
        self.client.login(username="admin", password="password")
        response = self.client.get("/analytics/?days=not-a-number")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["days"], 30)

    def test_analytics_churn_invalid_days_uses_default(self) -> None:
        self.client.login(username="admin", password="password")
        response = self.client.get("/analytics/churn/?days=not-a-number")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["days"], 30)

    def test_analytics_churn_requires_login(self) -> None:
        response = self.client.get("/analytics/churn/")
        self.assertEqual(response.status_code, 302)

    def test_analytics_churn_renders(self) -> None:
        self.client.login(username="admin", password="password")
        response = self.client.get("/analytics/churn/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Churn")


class TestCLIAnalyticsOverview(TestCase):
    def test_overview_json_output(self) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["analytics", "overview", "--days", "30", "--json"])
        self.assertEqual(result.exit_code, 0, msg=result.output)
        data = json.loads(result.output)
        self.assertTrue(data["ok"])
        self.assertIn("active_subscribers", data["data"])

    def test_overview_human_output(self) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["analytics", "overview", "--days", "7"])
        self.assertEqual(result.exit_code, 0, msg=result.output)
        self.assertIn("Overview", result.output)


class TestCLIAnalyticsChurn(TestCase):
    def test_churn_json_output(self) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["analytics", "churn", "--json"])
        self.assertEqual(result.exit_code, 0, msg=result.output)
        data = json.loads(result.output)
        self.assertTrue(data["ok"])
        self.assertIn("churn_rate", data["data"])


class TestCLIAnalyticsHealthReport(TestCase):
    def test_health_report_json(self) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["analytics", "health-report", "--json"])
        self.assertEqual(result.exit_code, 0, msg=result.output)
        data = json.loads(result.output)
        self.assertTrue(data["ok"])
        self.assertIn("healthy", data["data"])

    def test_health_report_human(self) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["analytics", "health-report"])
        self.assertEqual(result.exit_code, 0, msg=result.output)
        self.assertIn("Health score report", result.output)


class TestCLIAnalyticsCampaignStats(TestCase):
    def test_nonexistent_campaign_json(self) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["analytics", "campaign-stats", "99999", "--json"])
        self.assertNotEqual(result.exit_code, 0)
