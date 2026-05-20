"""Email template model for Postino.

Stores reusable email templates with subject, HTML body, and optional
plain-text body. Templates use Django template syntax and are rendered
at send time with subscriber context variables.
"""

from __future__ import annotations

from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.models import TimestampMixin


class EmailTemplate(TimestampMixin):
    """A reusable email template.

    The ``slug`` is the stable identifier used by campaigns and sequences
    to reference a template. The ``subject_default`` may contain Django
    template variables (e.g. ``{{ subscriber_name }}``) and is rendered
    alongside the body at send time.

    ``html_body`` and ``text_body`` contain the email content that will
    be rendered inside the base email layout. They are **not** full HTML
    documents — the footer, unsubscribe link, and branding are injected
    by the renderer.
    """

    name: models.CharField = models.CharField(
        max_length=200,
        verbose_name=_("name"),
        help_text=_("Human-readable template name."),
    )
    slug: models.SlugField = models.SlugField(
        unique=True,
        verbose_name=_("slug"),
        help_text=_("Stable identifier used by campaigns and sequences."),
    )
    subject_default: models.CharField = models.CharField(
        max_length=255,
        verbose_name=_("default subject"),
        help_text=_("Subject line, may contain {{ variable }} placeholders."),
    )
    html_body: models.TextField = models.TextField(
        verbose_name=_("HTML body"),
        help_text=_("Email body content rendered inside the base layout."),
    )
    text_body: models.TextField = models.TextField(
        blank=True,
        default="",
        verbose_name=_("plain text body"),
        help_text=_("Optional plain-text version. Left blank, HTML is sent only."),
    )

    class Meta:
        ordering = ["name"]
        verbose_name = _("email template")
        verbose_name_plural = _("email templates")

    def __str__(self) -> str:
        return f"{self.name} ({self.slug})"
