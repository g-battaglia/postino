"""Consent models for GDPR-compliant email type management.

Two append-only audit models --- ConsentRecord and UnsubscribeEvent --- form
the compliance backbone. Application code must never update or delete rows
from either table. The AppendOnlyQuerySet enforces this at the ORM level.
"""

from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.models import TimestampMixin
from apps.subscribers.models import Subscriber


class AppendOnlyQuerySet(models.QuerySet):
    """QuerySet that forbids bulk ``update()`` and ``delete()``.

    Append-only audit tables must never be mutated after creation.
    Calling ``.update()`` or ``.delete()`` on the queryset raises
    ``ValidationError`` so that bugs are caught early.
    """

    def update(self, **kwargs) -> int:  # type: ignore[override]
        raise ValidationError(
            _("Bulk updates are not allowed on append-only models."),
        )

    def delete(self) -> tuple[int, dict[str, int]]:
        raise ValidationError(
            _("Bulk deletes are not allowed on append-only models."),
        )


AppendOnlyManager = models.Manager.from_queryset(AppendOnlyQuerySet)


class _AppendOnlyModel(models.Model):
    """Abstract base that prevents save/delete on persisted instances.

    New instances can be created freely. Once a row has a primary key,
    calling ``.save()`` or ``.delete()`` on the instance raises
    ``ValidationError``.
    """

    objects = AppendOnlyManager()

    class Meta:
        abstract = True

    def save(self, **kwargs) -> None:  # type: ignore[override]
        if self.pk is not None:
            raise ValidationError(
                _("Cannot modify an existing %(model)s record."),
                params={"model": self.__class__.__name__},
            )
        super().save(**kwargs)

    def delete(self, **kwargs) -> tuple[int, dict[str, int]]:  # type: ignore[override]
        raise ValidationError(
            _("Cannot delete a %(model)s record."),
            params={"model": self.__class__.__name__},
        )


class EmailType(TimestampMixin):
    """A category of email used for granular consent and campaign classification.

    Examples: ``weekly_digest``, ``onboarding``, ``product_update``,
    ``transactional``. Each subscriber grants or withdraws consent per
    email type. Transactional types bypass marketing consent but still
    respect global unsubscribe.
    """

    slug = models.SlugField(unique=True)
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    is_transactional = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["slug"]
        verbose_name = _("email type")
        verbose_name_plural = _("email types")

    def __str__(self) -> str:
        return self.name or self.slug


class ConsentRecord(_AppendOnlyModel):
    """Append-only audit log of consent grants and withdrawals.

    Every consent action (grant or withdraw) creates a new row.
    Existing rows must never be updated or deleted --- this is the
    core GDPR compliance invariant.

    All FK fields use ``on_delete=PROTECT`` (even nullable ones) to
    prevent the database from silently mutating an existing audit row
    when a referenced object is deleted.  PLAN.md shows ``SET_NULL``
    for ``email_type``, but the append-only invariant wins: a
    SET_NULL would issue an implicit UPDATE on an immutable row.
    """

    class Action(models.TextChoices):
        GRANT = "grant", _("Grant")
        WITHDRAW = "withdraw", _("Withdraw")

    id = models.BigAutoField(primary_key=True)
    subscriber = models.ForeignKey(
        Subscriber,
        on_delete=models.PROTECT,
        related_name="consent_records",
        verbose_name=_("subscriber"),
    )
    email_type = models.ForeignKey(
        EmailType,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        verbose_name=_("email type"),
    )
    action = models.CharField(
        max_length=10,
        choices=Action.choices,
        verbose_name=_("action"),
    )
    method = models.CharField(
        max_length=100,
        verbose_name=_("method"),
    )
    ip_address = models.GenericIPAddressField(
        null=True,
        blank=True,
        verbose_name=_("IP address"),
    )
    proof = models.TextField(blank=True, verbose_name=_("proof"))
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(
                fields=["subscriber", "email_type", "-created_at"],
                name="consent_sub_type_created",
            ),
        ]
        verbose_name = _("consent record")
        verbose_name_plural = _("consent records")

    def __str__(self) -> str:
        return f"ConsentRecord {self.pk}: {self.action} for {self.subscriber_id}"


class UnsubscribeEvent(_AppendOnlyModel):
    """Immutable record of every unsubscribe action.

    Rows are never deleted, even during GDPR erasure. The email field
    preserves the address at the time of unsubscribe for compliance audits.

    All FK fields use ``on_delete=PROTECT`` (even nullable ones) to
    prevent the database from silently mutating an existing audit row
    when a referenced object is deleted.  PLAN.md shows ``SET_NULL``
    for ``subscriber`` and ``email_type``, but the append-only invariant
    wins: a SET_NULL would issue an implicit UPDATE on an immutable row.
    """

    id = models.BigAutoField(primary_key=True)
    subscriber = models.ForeignKey(
        Subscriber,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="unsubscribe_events",
        verbose_name=_("subscriber"),
    )
    email = models.EmailField(
        db_index=True,
        verbose_name=_("email"),
    )
    email_type = models.ForeignKey(
        EmailType,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        verbose_name=_("email type"),
    )
    method = models.CharField(
        max_length=100,
        verbose_name=_("method"),
    )
    ip_address = models.GenericIPAddressField(
        null=True,
        blank=True,
        verbose_name=_("IP address"),
    )
    user_agent = models.TextField(blank=True, verbose_name=_("user agent"))
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["email"], name="unsub_email"),
            models.Index(fields=["subscriber"], name="unsub_subscriber"),
        ]
        verbose_name = _("unsubscribe event")
        verbose_name_plural = _("unsubscribe events")

    def __str__(self) -> str:
        return f"UnsubscribeEvent {self.pk}: {self.email}"

    def save(self, **kwargs) -> None:  # type: ignore[override]
        self.email = self.email.strip().lower()
        super().save(**kwargs)
