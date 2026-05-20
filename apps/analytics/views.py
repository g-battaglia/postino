"""Analytics views for Postino.

All views require staff/admin login. Provides the analytics overview page,
churn dashboard, and HTMX partials. Server-side rendered with Django templates.
"""

from django.contrib.admin.views.decorators import staff_member_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render

from apps.analytics.services import (
    get_all_sequence_performances,
    get_churn_metrics,
    get_health_distribution,
    get_overview_metrics,
)


def _parse_days(value: str | None) -> int:
    """Parse a days query parameter with safe defaults and bounds."""
    try:
        days = int(value or 30)
    except (TypeError, ValueError):
        return 30
    return max(1, min(days, 365))


@staff_member_required
def analytics_index(request: HttpRequest) -> HttpResponse:
    """Render the analytics overview page with email metrics and charts."""
    days = _parse_days(request.GET.get("days"))
    metrics = get_overview_metrics(days=days)
    health_distribution = get_health_distribution()
    sequence_performances = get_all_sequence_performances()

    context = {
        "nav_active": "analytics",
        "metrics": metrics,
        "days": days,
        "health_distribution": health_distribution,
        "sequence_performances": sequence_performances,
    }
    return render(request, "analytics/index.html", context)


@staff_member_required
def analytics_churn(request: HttpRequest) -> HttpResponse:
    """Render the churn dashboard with health distribution and at-risk list."""
    days = _parse_days(request.GET.get("days"))
    churn = get_churn_metrics(days=days)
    health_distribution = get_health_distribution()

    context = {
        "nav_active": "churn",
        "churn": churn,
        "days": days,
        "health_distribution": health_distribution,
    }
    return render(request, "analytics/churn.html", context)
