"""Subscriber and Tag models for Postino.

Subscriber stores the email audience with status tracking, health scoring,
and source attribution. Tag provides manual and auto-tagging capabilities.
"""

from __future__ import annotations

import re
import uuid
from typing import Any

from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.models import TimestampMixin

_SUPPRESSED_STATUSES = frozenset({"unsubscribed", "bounced", "complained", "deleted"})


def _validate_hex_color(value: str) -> None:
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", value):
        raise ValidationError(
            _("%(value)s is not a valid hex color (expected #RRGGBB)"),
            params={"value": value},
        )


class Subscriber(TimestampMixin):
    """An email subscriber with status, health score, and source tracking.

    Suppression invariant: once a subscriber reaches a suppressed status
    (unsubscribed, bounced, complained, deleted), they cannot be reactivated
    through model save. The only path back is a new explicit signup with
    double opt-in (handled at the service layer).
    """

    class Status(models.TextChoices):
        PENDING = "pending", _("Pending")
        ACTIVE = "active", _("Active")
        UNSUBSCRIBED = "unsubscribed", _("Unsubscribed")
        BOUNCED = "bounced", _("Bounced")
        COMPLAINED = "complained", _("Complained")
        DELETED = "deleted", _("Deleted")

    class Source(models.TextChoices):
        MANUAL = "manual", _("Manual")
        IMPORT = "import", _("Import")
        SYNC = "sync", _("Sync")
        SIGNUP_FORM = "signup_form", _("Signup Form")

    id: models.UUIDField = models.UUIDField(primary_key=True, default=uuid.uuid4)
    email: models.EmailField = models.EmailField(unique=True, db_index=True)
    name: models.CharField = models.CharField(max_length=255, blank=True)

    status: models.CharField = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
    )
    source: models.CharField = models.CharField(
        max_length=20,
        choices=Source.choices,
        default=Source.MANUAL,
    )
    source_id: models.CharField = models.CharField(max_length=255, blank=True, db_index=True)
    metadata: models.JSONField = models.JSONField(default=dict)

    tags: models.ManyToManyField = models.ManyToManyField("Tag", blank=True)

    health_score: models.IntegerField = models.IntegerField(
        default=50,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
    )
    last_activity_at: models.DateTimeField = models.DateTimeField(null=True, blank=True)
    ip_address: models.GenericIPAddressField = models.GenericIPAddressField(
        null=True, blank=True,
    )

    double_optin_token: models.CharField = models.CharField(
        max_length=128, null=True, blank=True, unique=True,
    )
    double_optin_confirmed_at: models.DateTimeField = models.DateTimeField(
        null=True, blank=True,
    )

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status", "health_score"], name="sub_status_health"),
            models.Index(fields=["source", "source_id"], name="sub_source_sourceid"),
        ]

    def __str__(self) -> str:
        return self.email

    def clean(self) -> None:
        super().clean()
        self.email = self.email.strip().lower()

    def save(self, **kwargs: Any) -> None:
        self.email = self.email.strip().lower()
        self._run_field_validators()
        self._enforce_suppression_invariant()
        super().save(**kwargs)

    def _run_field_validators(self) -> None:
        errors: dict[str, list[str]] = {}
        for field in self._meta.fields:
            value = getattr(self, field.attname)
            try:
                field.run_validators(value)
            except ValidationError as e:
                errors[field.name] = e.messages
        if errors:
            raise ValidationError(errors)

    @property
    def is_suppressed(self) -> bool:
        return self.status in _SUPPRESSED_STATUSES

    def _enforce_suppression_invariant(self) -> None:
        if not self.pk:
            return
        try:
            current = Subscriber.objects.values_list("status", flat=True).get(pk=self.pk)
        except Subscriber.DoesNotExist:
            return
        if current in _SUPPRESSED_STATUSES and self.status not in _SUPPRESSED_STATUSES:
            raise ValidationError(
                _(
                    "Cannot reactivate a suppressed subscriber "
                    "(%(current)s -> %(proposed)s)."
                ),
                params={"current": current, "proposed": self.status},
            )


class Tag(TimestampMixin):
    """A label for grouping subscribers, with optional auto-tagging rules."""

    name: models.SlugField = models.SlugField(unique=True)
    display_name: models.CharField = models.CharField(max_length=100)
    color: models.CharField = models.CharField(
        max_length=7,
        default="#6366f1",
        validators=[_validate_hex_color],
    )
    auto_rule: models.JSONField = models.JSONField(null=True, blank=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.display_name or self.name


class DataSource(TimestampMixin):
    """An external data source for syncing subscribers into Postino.

    Configuration is stored as JSON and read from the TOML ``[[sources]]``
    sections. Each source defines a database connection, query, field mapping,
    and optional default tag.
    """

    class SourceType(models.TextChoices):
        DATABASE = "database", _("Database")

    name: models.CharField = models.CharField(max_length=200)
    source_type: models.CharField = models.CharField(
        max_length=20,
        choices=SourceType.choices,
        default=SourceType.DATABASE,
    )
    config: models.JSONField = models.JSONField(default=dict)
    is_active: models.BooleanField = models.BooleanField(default=True)
    sync_interval_hours: models.PositiveIntegerField = models.PositiveIntegerField(default=6)
    last_sync_at: models.DateTimeField = models.DateTimeField(null=True, blank=True)
    default_tag: models.ForeignKey = models.ForeignKey(
        Tag,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="data_sources",
    )

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class SyncLog(models.Model):
    """A log entry recording the outcome of a single sync run."""

    class Status(models.TextChoices):
        RUNNING = "running", _("Running")
        SUCCESS = "success", _("Success")
        ERROR = "error", _("Error")
        DRY_RUN = "dry_run", _("Dry Run")

    data_source: models.ForeignKey = models.ForeignKey(
        DataSource,
        on_delete=models.CASCADE,
        related_name="logs",
    )
    status: models.CharField = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.RUNNING,
    )
    new_count: models.IntegerField = models.IntegerField(default=0)
    updated_count: models.IntegerField = models.IntegerField(default=0)
    skipped_count: models.IntegerField = models.IntegerField(default=0)
    suppressed_count: models.IntegerField = models.IntegerField(default=0)
    error_details: models.JSONField = models.JSONField(null=True, blank=True)
    started_at: models.DateTimeField = models.DateTimeField()
    completed_at: models.DateTimeField = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-started_at"]

    def __str__(self) -> str:
        source_name = self.data_source.name if self.data_source_id else "unknown"
        return f"SyncLog({source_name}, {self.status}, {self.started_at})"
