"""Tests for the EmailType model and admin."""

from __future__ import annotations

import pytest
from django.contrib.admin.sites import AdminSite
from django.db import IntegrityError

from apps.consent.admin import EmailTypeAdmin
from apps.consent.models import EmailType

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------


class TestEmailTypeDefaults:
    def test_is_transactional_defaults_to_false(self) -> None:
        et = EmailType.objects.create(slug="newsletter", name="Newsletter")
        assert et.is_transactional is False

    def test_is_active_defaults_to_true(self) -> None:
        et = EmailType.objects.create(slug="newsletter", name="Newsletter")
        assert et.is_active is True

    def test_description_defaults_to_blank(self) -> None:
        et = EmailType.objects.create(slug="newsletter", name="Newsletter")
        assert et.description == ""

    def test_timestamps_are_set(self) -> None:
        et = EmailType.objects.create(slug="newsletter", name="Newsletter")
        assert et.created_at is not None
        assert et.updated_at is not None


# ---------------------------------------------------------------------------
# Uniqueness
# ---------------------------------------------------------------------------


class TestEmailTypeUniqueness:
    def test_slug_must_be_unique(self) -> None:
        EmailType.objects.create(slug="digest", name="Digest")
        with pytest.raises(IntegrityError):
            EmailType.objects.create(slug="digest", name="Another Digest")


# ---------------------------------------------------------------------------
# __str__
# ---------------------------------------------------------------------------


class TestEmailTypeStr:
    def test_str_returns_name(self) -> None:
        et = EmailType.objects.create(slug="promo", name="Promotions")
        assert str(et) == "Promotions"

    def test_str_falls_back_to_slug_when_name_is_empty(self) -> None:
        et = EmailType.objects.create(slug="fallback", name="")
        assert str(et) == "fallback"


# ---------------------------------------------------------------------------
# Ordering
# ---------------------------------------------------------------------------


class TestEmailTypeOrdering:
    def test_ordering_is_by_slug(self) -> None:
        EmailType.objects.create(slug="z-weekly", name="Weekly")
        EmailType.objects.create(slug="a-daily", name="Daily")
        EmailType.objects.create(slug="m-monthly", name="Monthly")
        slugs = list(EmailType.objects.values_list("slug", flat=True))
        assert slugs == ["a-daily", "m-monthly", "z-weekly"]


# ---------------------------------------------------------------------------
# Admin
# ---------------------------------------------------------------------------


class TestEmailTypeAdmin:
    def test_email_type_is_registered(self) -> None:
        from django.contrib import admin

        assert admin.site.is_registered(EmailType)

    def test_list_display_fields(self) -> None:
        ma = EmailTypeAdmin(EmailType, AdminSite())
        assert "slug" in ma.list_display
        assert "name" in ma.list_display
        assert "is_transactional" in ma.list_display
        assert "is_active" in ma.list_display

    def test_search_fields(self) -> None:
        ma = EmailTypeAdmin(EmailType, AdminSite())
        assert "slug" in ma.search_fields
        assert "name" in ma.search_fields

    def test_list_filter(self) -> None:
        ma = EmailTypeAdmin(EmailType, AdminSite())
        assert "is_transactional" in ma.list_filter
        assert "is_active" in ma.list_filter
