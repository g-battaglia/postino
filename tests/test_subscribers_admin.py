"""Tests for the subscribers Django admin configuration."""

from __future__ import annotations

import pytest
from django.contrib.admin.sites import AdminSite

from apps.subscribers.admin import SubscriberAdmin, TagAdmin
from apps.subscribers.models import Subscriber, Tag

pytestmark = pytest.mark.django_db


class TestSubscriberAdminRegistration:
    def test_subscriber_is_registered(self) -> None:
        from django.contrib import admin

        assert admin.site.is_registered(Subscriber)

    def test_tag_is_registered(self) -> None:
        from django.contrib import admin

        assert admin.site.is_registered(Tag)


class TestSubscriberAdminConfiguration:
    def test_list_display_fields(self) -> None:
        ma = SubscriberAdmin(Subscriber, AdminSite())
        assert "email" in ma.list_display
        assert "status" in ma.list_display
        assert "health_score" in ma.list_display

    def test_search_fields_include_email(self) -> None:
        ma = SubscriberAdmin(Subscriber, AdminSite())
        assert "email" in ma.search_fields

    def test_list_filter_includes_status(self) -> None:
        ma = SubscriberAdmin(Subscriber, AdminSite())
        assert "status" in ma.list_filter

    def test_status_readonly_when_suppressed(self) -> None:
        ma = SubscriberAdmin(Subscriber, AdminSite())
        sub = Subscriber.objects.create(
            email="sup@example.com",
            status=Subscriber.Status.UNSUBSCRIBED,
        )
        readonly = ma.get_readonly_fields(request=None, obj=sub)
        assert "status" in readonly

    def test_status_editable_when_active(self) -> None:
        ma = SubscriberAdmin(Subscriber, AdminSite())
        sub = Subscriber.objects.create(
            email="active@example.com",
            status=Subscriber.Status.ACTIVE,
        )
        readonly = ma.get_readonly_fields(request=None, obj=sub)
        assert "status" not in readonly

    def test_status_editable_for_new_object(self) -> None:
        ma = SubscriberAdmin(Subscriber, AdminSite())
        readonly = ma.get_readonly_fields(request=None, obj=None)
        assert "status" not in readonly


class TestTagAdminConfiguration:
    def test_list_display_fields(self) -> None:
        ma = TagAdmin(Tag, AdminSite())
        assert "name" in ma.list_display
        assert "display_name" in ma.list_display
