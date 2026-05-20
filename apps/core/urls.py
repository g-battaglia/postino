"""Core URL configuration -- dashboard, health check, settings, and shared endpoints."""

from django.urls import path

from apps.core.views import dashboard, health_check, set_language, settings_page

app_name = "core"

urlpatterns = [
    path("", dashboard, name="dashboard"),
    path("health/", health_check, name="health_check"),
    path("settings/", settings_page, name="settings"),
    path("settings/language/", set_language, name="set_language"),
]
