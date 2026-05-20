"""Core views for Postino.

Provides the health-check endpoint and the main dashboard view.
All dashboard views require staff/admin login.
"""

from django.conf import settings
from django.contrib.admin.views.decorators import staff_member_required
from django.http import HttpRequest, JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from apps.analytics.services import (
    get_at_risk_subscribers,
    get_growth_data,
    get_health_distribution,
    get_overview_metrics,
    get_recent_campaigns_with_stats,
)


def health_check(request: HttpRequest) -> JsonResponse:
    """Return a lightweight JSON payload for deployment health probes.

    Unauthenticated, side-effect free, and never exposes secrets.
    """
    payload: dict[str, str] = {"status": "ok"}
    version = getattr(settings, "POSTINO_VERSION", None)
    if version:
        payload["version"] = version
    return JsonResponse(payload)


@staff_member_required
def dashboard(request: HttpRequest):
    """Render the main dashboard overview page with live analytics data."""
    metrics = get_overview_metrics(days=30)
    health_distribution = get_health_distribution()
    growth_data = get_growth_data(months=6)
    recent_campaigns = get_recent_campaigns_with_stats(limit=5)
    at_risk_subscribers = get_at_risk_subscribers(limit=5)

    context = {
        "nav_active": "dashboard",
        "stats": {
            "active_subscribers": metrics.active_subscribers,
            "subscribers_trend": None,
            "emails_sent_30d": metrics.emails_sent,
            "emails_trend": None,
            "avg_health": metrics.avg_health_score,
            "health_meta": None,
            "churn_rate": f"{metrics.churn_rate}%",
            "churn_trend": None,
        },
        "growth_data": growth_data,
        "recent_campaigns": recent_campaigns,
        "health_distribution": health_distribution,
        "at_risk_subscribers": at_risk_subscribers,
    }
    return render(request, "dashboard/index.html", context)


@staff_member_required
def settings_page(request: HttpRequest):
    """Render the settings page with General, Email Types, Data Sources, and Language tabs."""
    from apps.consent.models import EmailType
    from apps.subscribers.models import DataSource
    from cli.config import load_config

    config = load_config()
    tab = request.GET.get("tab", "general")

    context = {
        "nav_active": "settings",
        "tab": tab,
        "general": {
            "app_name": config.branding.app_name,
            "timezone": config.server.timezone,
            "base_url": config.server.base_url,
            "provider": config.email.provider,
            "from_name": config.email.from_name,
            "from_email": config.email.from_email,
            "require_double_optin": config.gdpr.require_double_optin,
            "unsubscribed_retention_days": config.gdpr.unsubscribed_retention_days,
            "enable_open_tracking": config.gdpr.enable_open_tracking,
            "enable_click_tracking": config.gdpr.enable_click_tracking,
        },
        "email_types": EmailType.objects.order_by("slug"),
        "data_sources": DataSource.objects.order_by("name"),
        "available_languages": settings.LANGUAGES,
        "current_language": request.LANGUAGE_CODE,
    }
    return render(request, "settings/index.html", context)


@staff_member_required
@require_http_methods(["POST"])
def set_language(request: HttpRequest):
    """Set the user's preferred language via Django's i18n machinery."""
    from django.utils import translation

    lang_code = request.POST.get("language", "en")
    valid_codes = [code for code, _ in settings.LANGUAGES]
    if lang_code not in valid_codes:
        lang_code = "en"

    translation.activate(lang_code)
    response = redirect(request.POST.get("next", "/"))
    response.set_cookie(
        settings.LANGUAGE_COOKIE_NAME,
        lang_code,
        max_age=365 * 24 * 60 * 60,
    )
    return response
