"""Django admin configuration for the campaigns app."""

from __future__ import annotations

from django.contrib import admin

from .models import Campaign, EmailSend, Sequence, SequenceEnrollment, SequenceStep


@admin.register(Campaign)
class CampaignAdmin(admin.ModelAdmin):
    list_display = (
        "name", "status", "email_type", "template",
        "recipient_count", "scheduled_at", "sent_at",
    )
    list_filter = ("status", "email_type")
    list_select_related = ("email_type", "template")
    search_fields = ("name", "subject_line")
    readonly_fields = ("created_at", "updated_at")
    list_per_page = 50
    date_hierarchy = "created_at"


class SequenceStepInline(admin.TabularInline):
    model = SequenceStep
    extra = 0
    fields = ("order", "delay_hours", "email_type", "template", "subject_override")
    ordering = ("order",)


@admin.register(Sequence)
class SequenceAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "trigger_type", "is_active", "created_at")
    list_filter = ("trigger_type", "is_active")
    search_fields = ("name", "slug")
    readonly_fields = ("created_at", "updated_at")
    inlines = [SequenceStepInline]
    list_per_page = 50
    date_hierarchy = "created_at"


@admin.register(SequenceStep)
class SequenceStepAdmin(admin.ModelAdmin):
    list_display = ("sequence", "order", "delay_hours", "email_type", "template")
    list_filter = ("sequence",)
    list_select_related = ("sequence", "email_type", "template")
    ordering = ("sequence", "order")


@admin.register(SequenceEnrollment)
class SequenceEnrollmentAdmin(admin.ModelAdmin):
    list_display = (
        "subscriber", "sequence", "status", "current_step", "enrolled_at",
    )
    list_filter = ("status", "sequence")
    list_select_related = ("subscriber", "sequence", "current_step")
    search_fields = ("subscriber__email", "sequence__name")
    readonly_fields = ("enrolled_at", "created_at", "updated_at")
    date_hierarchy = "enrolled_at"
    list_per_page = 50


@admin.register(EmailSend)
class EmailSendAdmin(admin.ModelAdmin):
    list_display = (
        "id", "subscriber", "campaign", "sequence_step", "email_type",
        "status", "subject_line_used", "sent_at",
    )
    list_filter = ("status", "email_type")
    list_select_related = ("subscriber", "campaign", "sequence_step", "email_type")
    search_fields = ("subject_line_used", "provider_message_id", "subscriber__email")
    date_hierarchy = "sent_at"
    list_per_page = 100
