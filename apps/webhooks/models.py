"""WebhookEvent model for storing inbound webhook payloads.

Every webhook received from an email provider is persisted as a WebhookEvent
before processing. This provides an audit trail and enables retry of failed
events via the ``process_webhook_backlog`` management command.
"""

from __future__ import annotations

from django.db import models
from django.utils.translation import gettext_lazy as _


class WebhookEvent(models.Model):
    """A single inbound webhook event from an email provider.

    Rows are created on receipt and marked ``processed=True`` once the
    event has been handled (status update, suppression, etc.). Unprocessed
    events can be retried by the backlog management command.
    """

    provider: models.CharField = models.CharField(
        max_length=50,
        verbose_name=_("provider"),
    )
    event_type: models.CharField = models.CharField(
        max_length=100,
        verbose_name=_("event type"),
    )
    payload: models.JSONField = models.JSONField(
        verbose_name=_("payload"),
    )
    processed: models.BooleanField = models.BooleanField(
        default=False,
        verbose_name=_("processed"),
    )
    error_message: models.TextField = models.TextField(
        blank=True,
        default="",
        verbose_name=_("error message"),
    )
    created_at: models.DateTimeField = models.DateTimeField(
        auto_now_add=True,
        verbose_name=_("created at"),
    )

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(
                fields=["provider", "event_type", "-created_at"],
                name="webhook_prov_type_created",
            ),
        ]
        verbose_name = _("webhook event")
        verbose_name_plural = _("webhook events")

    def __str__(self) -> str:
        return f"WebhookEvent {self.pk}: {self.provider}/{self.event_type}"
