"""Tests for Subscriber and Tag models."""

from __future__ import annotations

import uuid

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError

from apps.subscribers.models import Subscriber, Tag

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Subscriber basics
# ---------------------------------------------------------------------------


class TestSubscriberDefaults:
    def test_uuid_primary_key(self) -> None:
        sub = Subscriber.objects.create(email="a@example.com")
        assert isinstance(sub.pk, uuid.UUID)
        assert sub.pk == sub.id

    def test_default_status_is_pending(self) -> None:
        sub = Subscriber.objects.create(email="a@example.com")
        assert sub.status == Subscriber.Status.PENDING

    def test_default_source_is_manual(self) -> None:
        sub = Subscriber.objects.create(email="a@example.com")
        assert sub.source == Subscriber.Source.MANUAL

    def test_default_health_score_is_50(self) -> None:
        sub = Subscriber.objects.create(email="a@example.com")
        assert sub.health_score == 50

    def test_default_metadata_is_empty_dict(self) -> None:
        sub = Subscriber.objects.create(email="a@example.com")
        assert sub.metadata == {}

    def test_optional_fields_default_to_none_or_blank(self) -> None:
        sub = Subscriber.objects.create(email="a@example.com")
        assert sub.name == ""
        assert sub.source_id == ""
        assert sub.last_activity_at is None
        assert sub.ip_address is None
        assert sub.double_optin_token is None
        assert sub.double_optin_confirmed_at is None

    def test_str_returns_email(self) -> None:
        sub = Subscriber.objects.create(email="user@example.com")
        assert str(sub) == "user@example.com"


# ---------------------------------------------------------------------------
# Email normalization
# ---------------------------------------------------------------------------


class TestEmailNormalization:
    def test_email_lowercased_on_save(self) -> None:
        sub = Subscriber.objects.create(email="User@Example.COM")
        assert sub.email == "user@example.com"

    def test_email_whitespace_stripped(self) -> None:
        sub = Subscriber.objects.create(email="  user@example.com  ")
        assert sub.email == "user@example.com"

    def test_email_uniqueness_is_case_insensitive(self) -> None:
        Subscriber.objects.create(email="user@example.com")
        with pytest.raises((IntegrityError, ValidationError)):
            Subscriber.objects.create(email="USER@EXAMPLE.COM")

    def test_email_normalization_on_update(self) -> None:
        sub = Subscriber.objects.create(email="first@example.com")
        sub.email = "UPDATED@EXAMPLE.COM"
        sub.save()
        sub.refresh_from_db()
        assert sub.email == "updated@example.com"


# ---------------------------------------------------------------------------
# Health score validation
# ---------------------------------------------------------------------------


class TestHealthScoreValidation:
    def test_health_score_rejects_below_zero(self) -> None:
        sub = Subscriber(email="a@example.com", health_score=-1)
        with pytest.raises(ValidationError):
            sub.save()

    def test_health_score_rejects_above_100(self) -> None:
        sub = Subscriber(email="a@example.com", health_score=101)
        with pytest.raises(ValidationError):
            sub.save()

    def test_health_score_accepts_zero(self) -> None:
        sub = Subscriber.objects.create(email="zero@example.com", health_score=0)
        assert sub.health_score == 0

    def test_health_score_accepts_100(self) -> None:
        sub = Subscriber.objects.create(email="hundred@example.com", health_score=100)
        assert sub.health_score == 100


# ---------------------------------------------------------------------------
# is_suppressed property
# ---------------------------------------------------------------------------


class TestIsSuppressed:
    @pytest.mark.parametrize(
        "status",
        [
            Subscriber.Status.UNSUBSCRIBED,
            Subscriber.Status.BOUNCED,
            Subscriber.Status.COMPLAINED,
            Subscriber.Status.DELETED,
        ],
    )
    def test_suppressed_statuses(self, status: str) -> None:
        sub = Subscriber.objects.create(email=f"{status}@example.com", status=status)
        assert sub.is_suppressed is True

    @pytest.mark.parametrize(
        "status",
        [Subscriber.Status.ACTIVE, Subscriber.Status.PENDING],
    )
    def test_non_suppressed_statuses(self, status: str) -> None:
        sub = Subscriber.objects.create(email=f"{status}@example.com", status=status)
        assert sub.is_suppressed is False


# ---------------------------------------------------------------------------
# Suppression invariant (no reactivation)
# ---------------------------------------------------------------------------


class TestSuppressionInvariant:
    @pytest.mark.parametrize(
        "suppressed_status",
        [
            Subscriber.Status.UNSUBSCRIBED,
            Subscriber.Status.BOUNCED,
            Subscriber.Status.COMPLAINED,
            Subscriber.Status.DELETED,
        ],
    )
    def test_cannot_reactivate_from_suppressed_status(self, suppressed_status: str) -> None:
        sub = Subscriber.objects.create(
            email=f"sup_{suppressed_status}@example.com",
            status=suppressed_status,
        )
        sub.status = Subscriber.Status.ACTIVE
        with pytest.raises(ValidationError, match="Cannot reactivate"):
            sub.save()

    @pytest.mark.parametrize(
        "suppressed_status",
        [
            Subscriber.Status.UNSUBSCRIBED,
            Subscriber.Status.BOUNCED,
            Subscriber.Status.COMPLAINED,
            Subscriber.Status.DELETED,
        ],
    )
    def test_cannot_reactivate_to_pending(self, suppressed_status: str) -> None:
        sub = Subscriber.objects.create(
            email=f"pend_{suppressed_status}@example.com",
            status=suppressed_status,
        )
        sub.status = Subscriber.Status.PENDING
        with pytest.raises(ValidationError, match="Cannot reactivate"):
            sub.save()

    def test_can_still_update_other_fields_while_suppressed(self) -> None:
        sub = Subscriber.objects.create(
            email="other@example.com",
            status=Subscriber.Status.UNSUBSCRIBED,
        )
        sub.name = "Updated Name"
        sub.health_score = 10
        sub.save()
        sub.refresh_from_db()
        assert sub.name == "Updated Name"
        assert sub.health_score == 10
        assert sub.status == Subscriber.Status.UNSUBSCRIBED

    def test_can_change_between_suppressed_statuses(self) -> None:
        sub = Subscriber.objects.create(
            email="between@example.com",
            status=Subscriber.Status.UNSUBSCRIBED,
        )
        sub.status = Subscriber.Status.BOUNCED
        sub.save()
        sub.refresh_from_db()
        assert sub.status == Subscriber.Status.BOUNCED

    def test_new_subscriber_can_be_created_active(self) -> None:
        sub = Subscriber.objects.create(
            email="new@example.com",
            status=Subscriber.Status.ACTIVE,
        )
        assert sub.status == Subscriber.Status.ACTIVE

    def test_new_subscriber_can_be_created_pending(self) -> None:
        sub = Subscriber.objects.create(
            email="newpending@example.com",
            status=Subscriber.Status.PENDING,
        )
        assert sub.status == Subscriber.Status.PENDING


# ---------------------------------------------------------------------------
# Tag model
# ---------------------------------------------------------------------------


class TestTagModel:
    def test_tag_defaults(self) -> None:
        tag = Tag.objects.create(name="newsletter", display_name="Newsletter")
        assert tag.color == "#6366f1"
        assert tag.auto_rule is None

    def test_tag_str_uses_display_name(self) -> None:
        tag = Tag.objects.create(name="pro", display_name="Pro Users")
        assert str(tag) == "Pro Users"

    def test_tag_str_falls_back_to_name(self) -> None:
        tag = Tag.objects.create(name="pro", display_name="")
        assert str(tag) == "pro"

    def test_tag_name_must_be_unique(self) -> None:
        Tag.objects.create(name="unique", display_name="First")
        with pytest.raises(IntegrityError):
            Tag.objects.create(name="unique", display_name="Second")

    def test_color_validation_accepts_valid_hex(self) -> None:
        tag = Tag(name="test1", display_name="Test", color="#abcdef")
        tag.full_clean()

    def test_color_validation_accepts_uppercase_hex(self) -> None:
        tag = Tag(name="test2", display_name="Test", color="#ABCDEF")
        tag.full_clean()

    def test_color_validation_rejects_short_hex(self) -> None:
        tag = Tag(name="test3", display_name="Test", color="#fff")
        with pytest.raises(ValidationError):
            tag.full_clean()

    def test_color_validation_rejects_no_hash(self) -> None:
        tag = Tag(name="test4", display_name="Test", color="abcdef")
        with pytest.raises(ValidationError):
            tag.full_clean()

    def test_color_validation_rejects_invalid_chars(self) -> None:
        tag = Tag(name="test5", display_name="Test", color="#gggggg")
        with pytest.raises(ValidationError):
            tag.full_clean()

    def test_color_validation_rejects_too_long(self) -> None:
        tag = Tag(name="test6", display_name="Test", color="#1234567")
        with pytest.raises(ValidationError):
            tag.full_clean()


# ---------------------------------------------------------------------------
# Subscriber-Tag relationship
# ---------------------------------------------------------------------------


class TestSubscriberTagRelationship:
    def test_subscriber_can_have_tags(self) -> None:
        tag = Tag.objects.create(name="vip", display_name="VIP")
        sub = Subscriber.objects.create(email="tagged@example.com")
        sub.tags.add(tag)
        assert tag in sub.tags.all()

    def test_subscriber_can_have_multiple_tags(self) -> None:
        t1 = Tag.objects.create(name="a", display_name="A")
        t2 = Tag.objects.create(name="b", display_name="B")
        sub = Subscriber.objects.create(email="multi@example.com")
        sub.tags.add(t1, t2)
        assert sub.tags.count() == 2

    def test_subscriber_can_be_created_with_no_tags(self) -> None:
        sub = Subscriber.objects.create(email="notags@example.com")
        assert sub.tags.count() == 0
