"""Admin registration for the webhooks app."""

from django.contrib import admin

from .models import WebhookEvent


@admin.register(WebhookEvent)
class WebhookEventAdmin(admin.ModelAdmin):
    list_display = ("pk", "provider", "event_type", "processed", "created_at")
    list_filter = ("provider", "event_type", "processed")
    search_fields = ("provider", "event_type")
    readonly_fields = ("payload", "created_at")
    ordering = ("-created_at",)
