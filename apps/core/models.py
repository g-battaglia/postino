"""Core models shared across all Postino apps.

Contains only abstract base classes. No concrete models and therefore
no migrations live in this app.
"""

from django.db import models


class TimestampMixin(models.Model):
    """Abstract mixin that adds ``created_at`` and ``updated_at`` fields.

    Every concrete model that cares about audit timestamps should inherit
    from this mixin rather than defining its own timestamp fields.
    """

    created_at: models.DateTimeField = models.DateTimeField(auto_now_add=True)
    updated_at: models.DateTimeField = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True
