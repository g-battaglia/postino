"""Django admin configuration for the subscribers app."""

from __future__ import annotations

from django.contrib import admin

from .models import DataSource, Subscriber, SyncLog, Tag

_SUPPRESSED_STATUSES = frozenset({"unsubscribed", "bounced", "complained", "deleted"})


@admin.register(Subscriber)
class SubscriberAdmin(admin.ModelAdmin):
    list_display = ("email", "name", "status", "source", "health_score", "created_at")
    list_filter = ("status", "source")
    search_fields = ("email", "name")
    readonly_fields = ("created_at", "updated_at")
    list_per_page = 50

    def get_readonly_fields(self, request, obj: Subscriber | None = None) -> tuple[str, ...]:
        base = list(self.readonly_fields)
        if obj and obj.status in _SUPPRESSED_STATUSES:
            base.append("status")
        return tuple(base)


@admin.register(Tag)
class TagAdmin(admin.ModelAdmin):
    list_display = ("name", "display_name", "color", "created_at")
    search_fields = ("name", "display_name")
    readonly_fields = ("created_at", "updated_at")
    prepopulated_fields = {"name": ("display_name",)}


@admin.register(DataSource)
class DataSourceAdmin(admin.ModelAdmin):
    list_display = ("name", "source_type", "is_active", "sync_interval_hours", "last_sync_at")
    list_filter = ("source_type", "is_active")
    readonly_fields = ("created_at", "updated_at")


@admin.register(SyncLog)
class SyncLogAdmin(admin.ModelAdmin):
    list_display = (
        "data_source", "status", "new_count", "updated_count",
        "skipped_count", "suppressed_count", "started_at",
    )
    list_filter = ("status",)
    readonly_fields = ("started_at", "completed_at")
