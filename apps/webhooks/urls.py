"""URL configuration for the webhooks app."""

from django.urls import path

from . import views

app_name = "webhooks"

urlpatterns = [
    path("resend/", views.resend_webhook, name="resend"),
]
