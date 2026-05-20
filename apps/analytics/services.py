"""Analytics aggregation services for Postino.

All queries are server-side, computed from Django ORM. No external analytics
scripts are used. Used by dashboard views, analytics pages, and CLI commands.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta

from django.db.models import Avg, Count, Q
from django.utils import timezone

from apps.campaigns.models import Campaign, EmailSend, Sequence, SequenceEnrollment
from apps.subscribers.models import Subscriber


@dataclass
class OverviewMetrics:
    """Aggregated overview metrics for the dashboard and CLI."""

    total_subscribers: int = 0
    active_subscribers: int = 0
    emails_sent: int = 0
    emails_delivered: int = 0
    emails_opened: int = 0
    emails_clicked: int = 0
    emails_bounced: int = 0
    emails_complained: int = 0
    avg_health_score: float = 0.0
    churned_count: int = 0
    new_count: int = 0
    open_rate: float = 0.0
    click_rate: float = 0.0
    bounce_rate: float = 0.0
    churn_rate: float = 0.0


def get_overview_metrics(days: int = 30) -> OverviewMetrics:
    """Compute overview metrics for the last N days."""
    now = timezone.now()
    since = now - timedelta(days=days)

    active_subscribers = Subscriber.objects.filter(status=Subscriber.Status.ACTIVE).count()
    total_subscribers = Subscriber.objects.count()

    sends_qs = EmailSend.objects.filter(sent_at__gte=since)
    emails_sent = sends_qs.count()
    emails_delivered = sends_qs.filter(
        status__in=["delivered", "opened", "clicked"]
    ).count()
    emails_opened = sends_qs.filter(status__in=["opened", "clicked"]).count()
    emails_clicked = sends_qs.filter(status="clicked").count()
    emails_bounced = sends_qs.filter(status="bounced").count()
    emails_complained = sends_qs.filter(status="complained").count()

    avg_health = (
        Subscriber.objects.filter(status=Subscriber.Status.ACTIVE).aggregate(
            avg=Avg("health_score")
        )["avg"]
        or 0.0
    )

    churned_count = Subscriber.objects.filter(
        status__in=["unsubscribed", "bounced", "complained", "deleted"],
        updated_at__gte=since,
    ).count()

    new_count = Subscriber.objects.filter(created_at__gte=since).count()

    open_rate = (emails_opened / emails_sent * 100) if emails_sent > 0 else 0.0
    click_rate = (emails_clicked / emails_sent * 100) if emails_sent > 0 else 0.0
    bounce_rate = (emails_bounced / emails_sent * 100) if emails_sent > 0 else 0.0
    churn_rate = (churned_count / active_subscribers * 100) if active_subscribers > 0 else 0.0

    return OverviewMetrics(
        total_subscribers=total_subscribers,
        active_subscribers=active_subscribers,
        emails_sent=emails_sent,
        emails_delivered=emails_delivered,
        emails_opened=emails_opened,
        emails_clicked=emails_clicked,
        emails_bounced=emails_bounced,
        emails_complained=emails_complained,
        avg_health_score=round(avg_health, 1),
        churned_count=churned_count,
        new_count=new_count,
        open_rate=round(open_rate, 1),
        click_rate=round(click_rate, 1),
        bounce_rate=round(bounce_rate, 1),
        churn_rate=round(churn_rate, 1),
    )


@dataclass
class HealthDistribution:
    """Health score distribution across the active subscriber base."""

    healthy_count: int = 0
    at_risk_count: int = 0
    critical_count: int = 0
    total: int = 0
    healthy_pct: float = 0.0
    at_risk_pct: float = 0.0
    critical_pct: float = 0.0


def get_health_distribution() -> HealthDistribution:
    """Compute health score distribution for active subscribers."""
    active = Subscriber.objects.filter(status=Subscriber.Status.ACTIVE)
    total = active.count()

    healthy_count = active.filter(health_score__gte=70).count()
    at_risk_count = active.filter(health_score__gte=40, health_score__lt=70).count()
    critical_count = active.filter(health_score__lt=40).count()

    return HealthDistribution(
        healthy_count=healthy_count,
        at_risk_count=at_risk_count,
        critical_count=critical_count,
        total=total,
        healthy_pct=round(healthy_count / total * 100, 1) if total > 0 else 0.0,
        at_risk_pct=round(at_risk_count / total * 100, 1) if total > 0 else 0.0,
        critical_pct=round(critical_count / total * 100, 1) if total > 0 else 0.0,
    )


@dataclass
class ChurnMetrics:
    """Churn analytics over a time window."""

    period_days: int = 30
    active_at_start: int = 0
    churned_in_period: int = 0
    new_in_period: int = 0
    net_change: int = 0
    churn_rate: float = 0.0
    churned_by_reason: dict[str, int] = field(default_factory=dict)
    at_risk_subscribers: list[dict] = field(default_factory=list)


def get_churn_metrics(days: int = 30) -> ChurnMetrics:
    """Compute churn metrics for the last N days."""
    now = timezone.now()
    since = now - timedelta(days=days)

    active_at_start = Subscriber.objects.filter(
        Q(status=Subscriber.Status.ACTIVE) & Q(created_at__lt=since)
    ).count()

    churned_qs = Subscriber.objects.filter(
        status__in=["unsubscribed", "bounced", "complained", "deleted"],
        updated_at__gte=since,
    )
    churned_in_period = churned_qs.count()

    churned_by_reason = dict(
        churned_qs.values("status").annotate(cnt=Count("id")).values_list("status", "cnt")
    )

    new_in_period = Subscriber.objects.filter(created_at__gte=since).count()
    net_change = new_in_period - churned_in_period
    churn_rate = (
        round(churned_in_period / active_at_start * 100, 1)
        if active_at_start > 0
        else 0.0
    )

    at_risk_subscribers = list(
        Subscriber.objects.filter(
            status=Subscriber.Status.ACTIVE,
            health_score__lt=40,
        )
        .order_by("health_score", "-last_activity_at")
        .values("id", "email", "name", "health_score", "last_activity_at")[:20]
    )

    return ChurnMetrics(
        period_days=days,
        active_at_start=active_at_start,
        churned_in_period=churned_in_period,
        new_in_period=new_in_period,
        net_change=net_change,
        churn_rate=churn_rate,
        churned_by_reason=churned_by_reason,
        at_risk_subscribers=at_risk_subscribers,
    )


@dataclass
class CampaignStats:
    """Per-campaign delivery and engagement stats."""

    campaign_id: int = 0
    campaign_name: str = ""
    status: str = ""
    recipient_count: int = 0
    sent_count: int = 0
    delivered_count: int = 0
    opened_count: int = 0
    clicked_count: int = 0
    bounced_count: int = 0
    complained_count: int = 0
    open_rate: float = 0.0
    click_rate: float = 0.0
    bounce_rate: float = 0.0


def get_campaign_stats(campaign_id: int) -> CampaignStats | None:
    """Compute stats for a single campaign."""
    try:
        campaign = Campaign.objects.get(pk=campaign_id)
    except Campaign.DoesNotExist:
        return None

    sends = EmailSend.objects.filter(campaign=campaign)
    sent_count = sends.count()
    delivered_count = sends.filter(status__in=["delivered", "opened", "clicked"]).count()
    opened_count = sends.filter(status__in=["opened", "clicked"]).count()
    clicked_count = sends.filter(status="clicked").count()
    bounced_count = sends.filter(status="bounced").count()
    complained_count = sends.filter(status="complained").count()

    return CampaignStats(
        campaign_id=campaign.pk,
        campaign_name=campaign.name,
        status=campaign.status,
        recipient_count=campaign.recipient_count,
        sent_count=sent_count,
        delivered_count=delivered_count,
        opened_count=opened_count,
        clicked_count=clicked_count,
        bounced_count=bounced_count,
        complained_count=complained_count,
        open_rate=round(opened_count / sent_count * 100, 1) if sent_count > 0 else 0.0,
        click_rate=round(clicked_count / sent_count * 100, 1) if sent_count > 0 else 0.0,
        bounce_rate=round(bounced_count / sent_count * 100, 1) if sent_count > 0 else 0.0,
    )


@dataclass
class SequencePerformance:
    """Performance stats for a single sequence."""

    sequence_id: int = 0
    sequence_name: str = ""
    sequence_slug: str = ""
    total_enrollments: int = 0
    active_enrollments: int = 0
    completed_enrollments: int = 0
    cancelled_enrollments: int = 0
    completion_rate: float = 0.0
    step_stats: list[dict] = field(default_factory=list)


def get_sequence_performance(sequence_id: int) -> SequencePerformance | None:
    """Compute performance stats for a single sequence."""
    try:
        sequence = Sequence.objects.get(pk=sequence_id)
    except Sequence.DoesNotExist:
        return None

    enrollments = SequenceEnrollment.objects.filter(sequence=sequence)
    total_enrollments = enrollments.count()
    active_enrollments = enrollments.filter(status="active").count()
    completed_enrollments = enrollments.filter(status="completed").count()
    cancelled_enrollments = enrollments.filter(status="cancelled").count()
    completion_rate = (
        round(completed_enrollments / total_enrollments * 100, 1)
        if total_enrollments > 0
        else 0.0
    )

    step_stats = []
    for step in sequence.steps.all().order_by("order"):
        step_sends = EmailSend.objects.filter(sequence_step=step)
        step_sent = step_sends.count()
        step_opened = step_sends.filter(status__in=["opened", "clicked"]).count()
        step_clicked = step_sends.filter(status="clicked").count()
        step_stats.append({
            "order": step.order,
            "subject": step.subject_override or step.template.subject_default,
            "delay_hours": step.delay_hours,
            "sent": step_sent,
            "opened": step_opened,
            "clicked": step_clicked,
            "open_rate": round(step_opened / step_sent * 100, 1) if step_sent > 0 else 0.0,
        })

    return SequencePerformance(
        sequence_id=sequence.pk,
        sequence_name=sequence.name,
        sequence_slug=sequence.slug,
        total_enrollments=total_enrollments,
        active_enrollments=active_enrollments,
        completed_enrollments=completed_enrollments,
        cancelled_enrollments=cancelled_enrollments,
        completion_rate=completion_rate,
        step_stats=step_stats,
    )


def get_all_sequence_performances() -> list[SequencePerformance]:
    """Compute performance stats for all sequences."""
    results = []
    for seq in Sequence.objects.all():
        perf = get_sequence_performance(seq.pk)
        if perf is not None:
            results.append(perf)
    return results


def get_growth_data(months: int = 6) -> list[dict]:
    """Generate monthly growth data (new vs churned) for the last N months."""
    now = timezone.now()
    data = []
    for i in range(months - 1, -1, -1):
        month_start = now.replace(day=1) - timedelta(days=30 * i)
        month_start = month_start.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        if i > 0:
            next_month = now.replace(day=1) - timedelta(days=30 * (i - 1))
            next_month = next_month.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        else:
            if month_start.month == 12:
                next_month = month_start.replace(year=month_start.year + 1, month=1)
            else:
                next_month = month_start.replace(month=month_start.month + 1)

        new_count = Subscriber.objects.filter(
            created_at__gte=month_start, created_at__lt=next_month
        ).count()
        churned_count = Subscriber.objects.filter(
            status__in=["unsubscribed", "bounced", "complained", "deleted"],
            updated_at__gte=month_start,
            updated_at__lt=next_month,
        ).count()

        data.append({
            "month": month_start.strftime("%b"),
            "new": new_count,
            "churned": churned_count,
        })
    return data


def get_recent_campaigns_with_stats(limit: int = 5) -> list[dict]:
    """Get recent campaigns with computed delivery stats."""
    campaigns = Campaign.objects.order_by("-created_at")[:limit]
    results = []
    for c in campaigns:
        sends = EmailSend.objects.filter(campaign=c)
        sent_count = sends.count()
        delivered = sends.filter(status__in=["delivered", "opened", "clicked"]).count()
        opened = sends.filter(status__in=["opened", "clicked"]).count()

        results.append({
            "name": c.name,
            "status": c.status,
            "recipient_count": c.recipient_count,
            "sent_at": c.sent_at,
            "delivered_pct": round(delivered / sent_count * 100, 1) if sent_count > 0 else None,
            "opened_pct": round(opened / sent_count * 100, 1) if sent_count > 0 else None,
        })
    return results


def get_at_risk_subscribers(limit: int = 5) -> list[Subscriber]:
    """Return the most at-risk active subscribers (lowest health scores)."""
    return list(
        Subscriber.objects.filter(
            status=Subscriber.Status.ACTIVE,
            health_score__lt=40,
        )
        .select_related()
        .order_by("health_score")[:limit]
    )
