"""Django admin configuration for the consent app.

Audit models (ConsentRecord, UnsubscribeEvent) are read-only in admin:
no add, change, or delete permissions. Records are created exclusively
by application services, never manually.
"""

from __future__ import annotations

from django.contrib import admin

from apps.consent.models import ConsentRecord, EmailType, UnsubscribeEvent


@admin.register(EmailType)
class EmailTypeAdmin(admin.ModelAdmin):
    list_display = ("slug", "name", "is_transactional", "is_active", "created_at")
    list_filter = ("is_transactional", "is_active")
    search_fields = ("slug", "name")
    prepopulated_fields = {"slug": ("name",)}


class _AuditReadOnlyAdmin(admin.ModelAdmin):
    """Base admin for append-only audit tables.

    Disables add, change, and delete so that admin users can only
    browse the audit trail. All modifications must go through the
    service layer.
    """

    def has_add_permission(self, request) -> bool:
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        return False

    def has_delete_permission(self, request, obj=None) -> bool:
        return False


@admin.register(ConsentRecord)
class ConsentRecordAdmin(_AuditReadOnlyAdmin):
    list_display = ("id", "subscriber", "email_type", "action", "method", "created_at")
    list_filter = ("action", "email_type")
    list_select_related = ("subscriber", "email_type")
    search_fields = ("subscriber__email", "method")
    date_hierarchy = "created_at"


@admin.register(UnsubscribeEvent)
class UnsubscribeEventAdmin(_AuditReadOnlyAdmin):
    list_display = ("id", "email", "subscriber", "email_type", "method", "created_at")
    list_filter = ("email_type", "method")
    list_select_related = ("subscriber", "email_type")
    search_fields = ("email", "subscriber__email")
    date_hierarchy = "created_at"
