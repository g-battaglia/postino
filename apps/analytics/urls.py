"""Analytics URL configuration."""

from django.urls import path

from apps.analytics import views

app_name = "analytics"

urlpatterns = [
    path("", views.analytics_index, name="index"),
    path("churn/", views.analytics_churn, name="churn"),
]
