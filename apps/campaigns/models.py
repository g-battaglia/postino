"""Campaign, Sequence, and EmailSend models for Postino.

Campaign represents a one-shot email blast with lifecycle states
(draft → scheduled → sending → sent / cancelled). Sequence models
support multi-step automated email flows with configurable triggers.
EmailSend logs every individual email dispatched, tracking per-recipient
delivery and engagement status.
"""

from __future__ import annotations

from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.consent.models import EmailType
from apps.core.models import TimestampMixin
from apps.subscribers.models import Subscriber
from apps.templates_mgr.models import EmailTemplate


class Campaign(TimestampMixin):
    """A one-shot email campaign sent to a filtered audience.

    Lifecycle: ``draft`` → ``scheduled`` → ``sending`` → ``sent``.
    A campaign can be ``cancelled`` from any pre-sent state.
    """

    class Status(models.TextChoices):
        DRAFT = "draft", _("Draft")
        SCHEDULED = "scheduled", _("Scheduled")
        SENDING = "sending", _("Sending")
        SENT = "sent", _("Sent")
        CANCELLED = "cancelled", _("Cancelled")

    name: models.CharField = models.CharField(
        max_length=200,
        verbose_name=_("name"),
    )
    status: models.CharField = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.DRAFT,
        verbose_name=_("status"),
        db_index=True,
    )
    email_type: models.ForeignKey = models.ForeignKey(
        EmailType,
        on_delete=models.PROTECT,
        related_name="campaigns",
        verbose_name=_("email type"),
    )
    template: models.ForeignKey = models.ForeignKey(
        EmailTemplate,
        on_delete=models.PROTECT,
        related_name="campaigns",
        verbose_name=_("template"),
    )
    subject_line: models.CharField = models.CharField(
        max_length=255,
        verbose_name=_("subject line"),
    )
    audience_filter: models.JSONField = models.JSONField(
        default=dict,
        blank=True,
        verbose_name=_("audience filter"),
        help_text=_("JSON filter criteria for selecting recipients."),
    )
    recipient_count: models.PositiveIntegerField = models.PositiveIntegerField(
        default=0,
        verbose_name=_("recipient count"),
    )
    scheduled_at: models.DateTimeField = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name=_("scheduled at"),
    )
    sent_at: models.DateTimeField = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name=_("sent at"),
    )

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status"], name="campaign_status"),
            models.Index(fields=["-scheduled_at"], name="campaign_scheduled"),
        ]
        verbose_name = _("campaign")
        verbose_name_plural = _("campaigns")

    def __str__(self) -> str:
        return f"{self.name} [{self.get_status_display()}]"


class Sequence(TimestampMixin):
    """A multi-step automated email sequence with configurable triggers.

    Triggers: ``subscriber_created``, ``tag_added``, or ``manual``.
    trigger_config specifies matching criteria (e.g. ``{"tags": ["pro"]}``).
    """

    class TriggerType(models.TextChoices):
        SUBSCRIBER_CREATED = "subscriber_created", _("Subscriber Created")
        TAG_ADDED = "tag_added", _("Tag Added")
        MANUAL = "manual", _("Manual")

    name: models.CharField = models.CharField(
        max_length=200,
        verbose_name=_("name"),
    )
    slug: models.SlugField = models.SlugField(
        unique=True,
        verbose_name=_("slug"),
    )
    description: models.TextField = models.TextField(
        blank=True,
        default="",
        verbose_name=_("description"),
    )
    is_active: models.BooleanField = models.BooleanField(
        default=True,
        verbose_name=_("is active"),
    )
    trigger_type: models.CharField = models.CharField(
        max_length=30,
        choices=TriggerType.choices,
        verbose_name=_("trigger type"),
    )
    trigger_config: models.JSONField = models.JSONField(
        default=dict,
        blank=True,
        verbose_name=_("trigger config"),
        help_text=_(
            'Trigger matching rules, e.g. {"tags": ["pro"], "source": "signup_form"}.'
        ),
    )

    class Meta:
        ordering = ["-created_at"]
        verbose_name = _("sequence")
        verbose_name_plural = _("sequences")

    def __str__(self) -> str:
        return self.name


class SequenceStep(models.Model):
    """A single step within a sequence — one email sent after a delay.

    condition is an optional JSON filter evaluated before sending:
    e.g. ``{"health_below": 30}`` or ``{"has_tag": "at-risk"}``.
    """

    sequence: models.ForeignKey = models.ForeignKey(
        Sequence,
        on_delete=models.CASCADE,
        related_name="steps",
        verbose_name=_("sequence"),
    )
    order: models.PositiveIntegerField = models.PositiveIntegerField(
        verbose_name=_("order"),
    )
    delay_hours: models.PositiveIntegerField = models.PositiveIntegerField(
        default=0,
        verbose_name=_("delay hours"),
        help_text=_("Hours to wait after enrollment before sending this step."),
    )
    email_type: models.ForeignKey = models.ForeignKey(
        EmailType,
        on_delete=models.PROTECT,
        related_name="sequence_steps",
        verbose_name=_("email type"),
    )
    template: models.ForeignKey = models.ForeignKey(
        EmailTemplate,
        on_delete=models.PROTECT,
        related_name="sequence_steps",
        verbose_name=_("template"),
    )
    subject_override: models.CharField = models.CharField(
        max_length=255,
        blank=True,
        default="",
        verbose_name=_("subject override"),
        help_text=_("Leave blank to use the template's default subject."),
    )
    condition: models.JSONField = models.JSONField(
        null=True,
        blank=True,
        verbose_name=_("condition"),
        help_text=_(
            'Optional JSON filter to evaluate before sending, e.g. '
            '{"health_below": 30}.'
        ),
    )

    class Meta:
        ordering = ["order"]
        unique_together = [("sequence", "order")]
        verbose_name = _("sequence step")
        verbose_name_plural = _("sequence steps")

    def __str__(self) -> str:
        return f"Step {self.order}: {self.sequence}"


class SequenceEnrollment(TimestampMixin):
    """Tracks a subscriber's progress through a sequence.

    An enrollment is created when a trigger fires or manually via CLI.
    Auto-cancelled when the subscriber is suppressed/unsubscribed.
    """

    class Status(models.TextChoices):
        ACTIVE = "active", _("Active")
        COMPLETED = "completed", _("Completed")
        CANCELLED = "cancelled", _("Cancelled")
        PAUSED = "paused", _("Paused")

    subscriber: models.ForeignKey = models.ForeignKey(
        Subscriber,
        on_delete=models.CASCADE,
        related_name="sequence_enrollments",
        verbose_name=_("subscriber"),
    )
    sequence: models.ForeignKey = models.ForeignKey(
        Sequence,
        on_delete=models.CASCADE,
        related_name="enrollments",
        verbose_name=_("sequence"),
    )
    current_step: models.ForeignKey = models.ForeignKey(
        SequenceStep,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="active_enrollments",
        verbose_name=_("current step"),
    )
    status: models.CharField = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.ACTIVE,
        verbose_name=_("status"),
        db_index=True,
    )
    enrolled_at: models.DateTimeField = models.DateTimeField(
        auto_now_add=True,
        verbose_name=_("enrolled at"),
    )
    completed_at: models.DateTimeField = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name=_("completed at"),
    )

    class Meta:
        unique_together = [("subscriber", "sequence")]
        indexes = [
            models.Index(fields=["status"], name="seqenrollment_status"),
        ]
        verbose_name = _("sequence enrollment")
        verbose_name_plural = _("sequence enrollments")

    def __str__(self) -> str:
        return f"{self.subscriber} in {self.sequence} [{self.get_status_display()}]"


class EmailSend(models.Model):
    """An individual email dispatch log entry.

    Tracks the lifecycle of each email sent to a subscriber — from queued
    through delivery and engagement events (opened, clicked, bounced,
    complained). Linked to a Campaign or a SequenceStep.
    """

    class Status(models.TextChoices):
        QUEUED = "queued", _("Queued")
        SENT = "sent", _("Sent")
        DELIVERED = "delivered", _("Delivered")
        OPENED = "opened", _("Opened")
        CLICKED = "clicked", _("Clicked")
        BOUNCED = "bounced", _("Bounced")
        COMPLAINED = "complained", _("Complained")
        FAILED = "failed", _("Failed")

    id = models.BigAutoField(primary_key=True)
    subscriber: models.ForeignKey = models.ForeignKey(
        Subscriber,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="email_sends",
        verbose_name=_("subscriber"),
    )
    campaign: models.ForeignKey = models.ForeignKey(
        Campaign,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="email_sends",
        verbose_name=_("campaign"),
    )
    sequence_step: models.ForeignKey = models.ForeignKey(
        SequenceStep,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="email_sends",
        verbose_name=_("sequence step"),
    )
    email_type: models.ForeignKey = models.ForeignKey(
        EmailType,
        on_delete=models.PROTECT,
        related_name="email_sends",
        verbose_name=_("email type"),
    )
    subject_line_used: models.CharField = models.CharField(
        max_length=255,
        verbose_name=_("subject line used"),
    )
    provider_message_id: models.CharField = models.CharField(
        max_length=200,
        blank=True,
        default="",
        db_index=True,
        verbose_name=_("provider message ID"),
    )
    status: models.CharField = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.QUEUED,
        verbose_name=_("status"),
    )
    error_message: models.TextField = models.TextField(
        blank=True,
        default="",
        verbose_name=_("error message"),
    )
    sent_at: models.DateTimeField = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name=_("sent at"),
    )
    delivered_at: models.DateTimeField = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name=_("delivered at"),
    )
    opened_at: models.DateTimeField = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name=_("opened at"),
    )
    clicked_at: models.DateTimeField = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name=_("clicked at"),
    )
    bounced_at: models.DateTimeField = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name=_("bounced at"),
    )
    complained_at: models.DateTimeField = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name=_("complained at"),
    )

    class Meta:
        ordering = ["-sent_at"]
        indexes = [
            models.Index(fields=["subscriber", "-sent_at"], name="emailsend_sub_sent"),
            models.Index(fields=["campaign"], name="emailsend_campaign"),
            models.Index(fields=["provider_message_id"], name="emailsend_provider_id"),
        ]
        verbose_name = _("email send")
        verbose_name_plural = _("email sends")

    def __str__(self) -> str:
        return f"EmailSend {self.pk}: {self.subject_line_used} [{self.get_status_display()}]"
