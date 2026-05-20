"""Django admin configuration for the templates_mgr app."""

from __future__ import annotations

from django.contrib import admin

from .models import EmailTemplate


@admin.register(EmailTemplate)
class EmailTemplateAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "subject_default", "created_at", "updated_at")
    search_fields = ("name", "slug", "subject_default")
    readonly_fields = ("created_at", "updated_at")
    prepopulated_fields = {"slug": ("name",)}
    list_per_page = 50
