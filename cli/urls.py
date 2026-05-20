"""
Root URL configuration for Postino.

Dashboard URLs require authentication. Public URLs (unsubscribe, preferences,
webhooks, health) are accessible without login.

Additional URL prefixes (preferences/, sequences/, etc.) will be added
by each app's urls.py in later phases.
"""

from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include("apps.core.urls")),
    path("subscribers/", include("apps.subscribers.urls")),
    path("", include("apps.consent.urls")),
    path("campaigns/", include("apps.campaigns.urls")),
    path("sequences/", include("apps.campaigns.sequence_urls")),
    path("templates/", include("apps.templates_mgr.urls")),
    path("analytics/", include("apps.analytics.urls")),
    path("webhooks/", include("apps.webhooks.urls")),
]
