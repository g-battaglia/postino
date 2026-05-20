"""Tests for append-only ConsentRecord and UnsubscribeEvent models.

Covers:
- Model creation and field behaviour
- Append-only enforcement (model save/delete, queryset update/delete)
- No ``updated_at`` field on audit models
- Email normalization on UnsubscribeEvent
- Admin registration and read-only permissions
- Database indexes as specified in PLAN.md
"""

from __future__ import annotations

import pytest
from django.contrib.admin.sites import AdminSite
from django.core.exceptions import ValidationError
from django.db.models import ProtectedError

from apps.consent.admin import ConsentRecordAdmin, UnsubscribeEventAdmin
from apps.consent.models import ConsentRecord, EmailType, UnsubscribeEvent
from apps.subscribers.models import Subscriber

pytestmark = pytest.mark.django_db


class TestConsentRecordCreation:
    def test_create_grant_record(self) -> None:
        sub = Subscriber.objects.create(email="user@example.com")
        et = EmailType.objects.create(slug="newsletter", name="Newsletter")
        record = ConsentRecord.objects.create(
            subscriber=sub,
            email_type=et,
            action=ConsentRecord.Action.GRANT,
            method="web_form",
            ip_address="192.168.1.1",
            proof="Checkbox ticked on /signup",
        )
        assert record.pk is not None
        assert record.action == "grant"
        assert record.subscriber == sub
        assert record.email_type == et
        assert record.method == "web_form"
        assert record.ip_address == "192.168.1.1"
        assert record.proof == "Checkbox ticked on /signup"
        assert record.created_at is not None

    def test_create_withdraw_record(self) -> None:
        sub = Subscriber.objects.create(email="revoke@example.com")
        record = ConsentRecord.objects.create(
            subscriber=sub,
            action=ConsentRecord.Action.WITHDRAW,
            method="preference_center",
        )
        assert record.action == "withdraw"

    def test_email_type_can_be_null(self) -> None:
        sub = Subscriber.objects.create(email="notype@example.com")
        record = ConsentRecord.objects.create(
            subscriber=sub,
            action=ConsentRecord.Action.GRANT,
            method="import",
        )
        record.refresh_from_db()
        assert record.email_type is None

    def test_ip_address_can_be_null(self) -> None:
        sub = Subscriber.objects.create(email="noip@example.com")
        record = ConsentRecord.objects.create(
            subscriber=sub,
            action=ConsentRecord.Action.GRANT,
            method="cli",
        )
        record.refresh_from_db()
        assert record.ip_address is None

    def test_str_includes_pk_and_action(self) -> None:
        sub = Subscriber.objects.create(email="str@example.com")
        record = ConsentRecord.objects.create(
            subscriber=sub,
            action=ConsentRecord.Action.GRANT,
            method="test",
        )
        text = str(record)
        assert "grant" in text
        assert str(record.pk) in text

    def test_ordering_newest_first(self) -> None:
        sub = Subscriber.objects.create(email="ordered@example.com")
        r1 = ConsentRecord.objects.create(
            subscriber=sub, action=ConsentRecord.Action.GRANT, method="test",
        )
        r2 = ConsentRecord.objects.create(
            subscriber=sub, action=ConsentRecord.Action.WITHDRAW, method="test",
        )
        records = list(ConsentRecord.objects.all())
        assert records[0] == r2
        assert records[1] == r1


class TestConsentRecordNoUpdatedAt:
    def test_has_created_at_field(self) -> None:
        field_names = [f.name for f in ConsentRecord._meta.get_fields()]
        assert "created_at" in field_names

    def test_has_no_updated_at_field(self) -> None:
        field_names = [f.name for f in ConsentRecord._meta.get_fields()]
        assert "updated_at" not in field_names


class TestConsentRecordAppendOnly:
    def test_save_existing_record_raises(self) -> None:
        sub = Subscriber.objects.create(email="save-exist@example.com")
        record = ConsentRecord.objects.create(
            subscriber=sub,
            action=ConsentRecord.Action.GRANT,
            method="test",
        )
        record.action = ConsentRecord.Action.WITHDRAW
        with pytest.raises(ValidationError, match="Cannot modify"):
            record.save()

    def test_delete_existing_record_raises(self) -> None:
        sub = Subscriber.objects.create(email="del-single@example.com")
        record = ConsentRecord.objects.create(
            subscriber=sub,
            action=ConsentRecord.Action.GRANT,
            method="test",
        )
        with pytest.raises(ValidationError, match="Cannot delete"):
            record.delete()

    def test_queryset_update_raises(self) -> None:
        sub = Subscriber.objects.create(email="qs-update@example.com")
        ConsentRecord.objects.create(
            subscriber=sub,
            action=ConsentRecord.Action.GRANT,
            method="test",
        )
        with pytest.raises(ValidationError, match="Bulk updates"):
            ConsentRecord.objects.update(method="hacked")

    def test_queryset_delete_raises(self) -> None:
        sub = Subscriber.objects.create(email="qs-del@example.com")
        ConsentRecord.objects.create(
            subscriber=sub,
            action=ConsentRecord.Action.GRANT,
            method="test",
        )
        with pytest.raises(ValidationError, match="Bulk deletes"):
            ConsentRecord.objects.delete()

    def test_all_queryset_delete_raises(self) -> None:
        sub = Subscriber.objects.create(email="all-del@example.com")
        ConsentRecord.objects.create(
            subscriber=sub,
            action=ConsentRecord.Action.GRANT,
            method="test",
        )
        with pytest.raises(ValidationError, match="Bulk deletes"):
            ConsentRecord.objects.all().delete()

    def test_filter_queryset_delete_raises(self) -> None:
        sub = Subscriber.objects.create(email="filter-del@example.com")
        ConsentRecord.objects.create(
            subscriber=sub,
            action=ConsentRecord.Action.GRANT,
            method="test",
        )
        with pytest.raises(ValidationError, match="Bulk deletes"):
            ConsentRecord.objects.filter(subscriber=sub).delete()


class TestConsentRecordIndexes:
    def test_subscriber_email_type_created_index_exists(self) -> None:
        index_names = [idx.name for idx in ConsentRecord._meta.indexes]
        assert "consent_sub_type_created" in index_names

    def test_index_fields_correct(self) -> None:
        for idx in ConsentRecord._meta.indexes:
            if idx.name == "consent_sub_type_created":
                assert idx.fields == ["subscriber", "email_type", "-created_at"]
                return
        pytest.fail("consent_sub_type_created index not found")


class TestUnsubscribeEventCreation:
    def test_create_event(self) -> None:
        sub = Subscriber.objects.create(email="unsub@example.com")
        event = UnsubscribeEvent.objects.create(
            subscriber=sub,
            email="unsub@example.com",
            method="link",
            ip_address="10.0.0.1",
            user_agent="Mozilla/5.0",
        )
        assert event.pk is not None
        assert event.subscriber == sub
        assert event.email == "unsub@example.com"
        assert event.method == "link"
        assert event.ip_address == "10.0.0.1"
        assert event.user_agent == "Mozilla/5.0"
        assert event.created_at is not None

    def test_email_normalized_to_lowercase(self) -> None:
        UnsubscribeEvent.objects.create(
            email="  USER@Example.COM  ",
            method="link",
        )
        event = UnsubscribeEvent.objects.get()
        assert event.email == "user@example.com"

    def test_subscriber_can_be_null(self) -> None:
        event = UnsubscribeEvent.objects.create(
            email="nosub@example.com",
            method="one_click",
        )
        event.refresh_from_db()
        assert event.subscriber is None

    def test_email_type_can_be_null(self) -> None:
        event = UnsubscribeEvent.objects.create(
            email="notype@example.com",
            method="link",
        )
        event.refresh_from_db()
        assert event.email_type is None

    def test_str_includes_pk_and_email(self) -> None:
        event = UnsubscribeEvent.objects.create(
            email="display@example.com",
            method="test",
        )
        text = str(event)
        assert "display@example.com" in text
        assert str(event.pk) in text

    def test_ordering_newest_first(self) -> None:
        e1 = UnsubscribeEvent.objects.create(email="first@example.com", method="a")
        e2 = UnsubscribeEvent.objects.create(email="second@example.com", method="b")
        events = list(UnsubscribeEvent.objects.all())
        assert events[0] == e2
        assert events[1] == e1


class TestUnsubscribeEventNoUpdatedAt:
    def test_has_created_at_field(self) -> None:
        field_names = [f.name for f in UnsubscribeEvent._meta.get_fields()]
        assert "created_at" in field_names

    def test_has_no_updated_at_field(self) -> None:
        field_names = [f.name for f in UnsubscribeEvent._meta.get_fields()]
        assert "updated_at" not in field_names


class TestUnsubscribeEventAppendOnly:
    def test_save_existing_record_raises(self) -> None:
        event = UnsubscribeEvent.objects.create(
            email="save-exist@example.com", method="test",
        )
        event.method = "hacked"
        with pytest.raises(ValidationError, match="Cannot modify"):
            event.save()

    def test_delete_existing_record_raises(self) -> None:
        event = UnsubscribeEvent.objects.create(
            email="del-event@example.com", method="test",
        )
        with pytest.raises(ValidationError, match="Cannot delete"):
            event.delete()

    def test_queryset_update_raises(self) -> None:
        UnsubscribeEvent.objects.create(
            email="qs-upd@example.com", method="test",
        )
        with pytest.raises(ValidationError, match="Bulk updates"):
            UnsubscribeEvent.objects.update(method="hacked")

    def test_queryset_delete_raises(self) -> None:
        UnsubscribeEvent.objects.create(
            email="qs-del@example.com", method="test",
        )
        with pytest.raises(ValidationError, match="Bulk deletes"):
            UnsubscribeEvent.objects.delete()

    def test_all_queryset_delete_raises(self) -> None:
        UnsubscribeEvent.objects.create(
            email="all-del@example.com", method="test",
        )
        with pytest.raises(ValidationError, match="Bulk deletes"):
            UnsubscribeEvent.objects.all().delete()

    def test_filter_queryset_delete_raises(self) -> None:
        UnsubscribeEvent.objects.create(
            email="filter-del@example.com", method="test",
        )
        with pytest.raises(ValidationError, match="Bulk deletes"):
            UnsubscribeEvent.objects.filter(email="filter-del@example.com").delete()


class TestUnsubscribeEventIndexes:
    def test_email_index_exists(self) -> None:
        index_names = [idx.name for idx in UnsubscribeEvent._meta.indexes]
        assert "unsub_email" in index_names

    def test_subscriber_index_exists(self) -> None:
        index_names = [idx.name for idx in UnsubscribeEvent._meta.indexes]
        assert "unsub_subscriber" in index_names


class TestConsentRecordAdmin:
    def test_model_is_registered(self) -> None:
        from django.contrib import admin

        assert admin.site.is_registered(ConsentRecord)

    def test_has_no_add_permission(self) -> None:
        ma = ConsentRecordAdmin(ConsentRecord, AdminSite())
        assert ma.has_add_permission(None) is False

    def test_has_no_change_permission(self) -> None:
        ma = ConsentRecordAdmin(ConsentRecord, AdminSite())
        assert ma.has_change_permission(None) is False

    def test_has_no_delete_permission(self) -> None:
        ma = ConsentRecordAdmin(ConsentRecord, AdminSite())
        assert ma.has_delete_permission(None) is False

    def test_list_display_fields(self) -> None:
        ma = ConsentRecordAdmin(ConsentRecord, AdminSite())
        assert "id" in ma.list_display
        assert "subscriber" in ma.list_display
        assert "action" in ma.list_display
        assert "created_at" in ma.list_display

    def test_search_fields_include_subscriber_email(self) -> None:
        ma = ConsentRecordAdmin(ConsentRecord, AdminSite())
        assert "subscriber__email" in ma.search_fields

    def test_date_hierarchy(self) -> None:
        ma = ConsentRecordAdmin(ConsentRecord, AdminSite())
        assert ma.date_hierarchy == "created_at"

    def test_change_permission_denied_with_obj(self) -> None:
        sub = Subscriber.objects.create(email="admintest@example.com")
        record = ConsentRecord.objects.create(
            subscriber=sub, action=ConsentRecord.Action.GRANT, method="test",
        )
        ma = ConsentRecordAdmin(ConsentRecord, AdminSite())
        assert ma.has_change_permission(None, obj=record) is False

    def test_delete_permission_denied_with_obj(self) -> None:
        sub = Subscriber.objects.create(email="admin2@example.com")
        record = ConsentRecord.objects.create(
            subscriber=sub, action=ConsentRecord.Action.GRANT, method="test",
        )
        ma = ConsentRecordAdmin(ConsentRecord, AdminSite())
        assert ma.has_delete_permission(None, obj=record) is False


class TestUnsubscribeEventAdmin:
    def test_model_is_registered(self) -> None:
        from django.contrib import admin

        assert admin.site.is_registered(UnsubscribeEvent)

    def test_has_no_add_permission(self) -> None:
        ma = UnsubscribeEventAdmin(UnsubscribeEvent, AdminSite())
        assert ma.has_add_permission(None) is False

    def test_has_no_change_permission(self) -> None:
        ma = UnsubscribeEventAdmin(UnsubscribeEvent, AdminSite())
        assert ma.has_change_permission(None) is False

    def test_has_no_delete_permission(self) -> None:
        ma = UnsubscribeEventAdmin(UnsubscribeEvent, AdminSite())
        assert ma.has_delete_permission(None) is False

    def test_list_display_fields(self) -> None:
        ma = UnsubscribeEventAdmin(UnsubscribeEvent, AdminSite())
        assert "id" in ma.list_display
        assert "email" in ma.list_display
        assert "subscriber" in ma.list_display
        assert "created_at" in ma.list_display

    def test_search_fields_include_email(self) -> None:
        ma = UnsubscribeEventAdmin(UnsubscribeEvent, AdminSite())
        assert "email" in ma.search_fields

    def test_date_hierarchy(self) -> None:
        ma = UnsubscribeEventAdmin(UnsubscribeEvent, AdminSite())
        assert ma.date_hierarchy == "created_at"


class TestConsentRecordFKProtection:
    def test_cannot_delete_subscriber_with_consent_records(self) -> None:
        sub = Subscriber.objects.create(email="protected@example.com")
        ConsentRecord.objects.create(
            subscriber=sub,
            action=ConsentRecord.Action.GRANT,
            method="test",
        )
        with pytest.raises(ProtectedError):
            sub.delete()

    def test_cannot_delete_email_type_referenced_by_consent_record(self) -> None:
        sub = Subscriber.objects.create(email="et-protect@example.com")
        et = EmailType.objects.create(slug="promo", name="Promo")
        ConsentRecord.objects.create(
            subscriber=sub,
            email_type=et,
            action=ConsentRecord.Action.GRANT,
            method="test",
        )
        with pytest.raises(ProtectedError):
            et.delete()

    def test_email_type_still_present_after_failed_delete(self) -> None:
        sub = Subscriber.objects.create(email="et-persist@example.com")
        et = EmailType.objects.create(slug="weekly", name="Weekly")
        record = ConsentRecord.objects.create(
            subscriber=sub,
            email_type=et,
            action=ConsentRecord.Action.GRANT,
            method="test",
        )
        with pytest.raises(ProtectedError):
            et.delete()
        record.refresh_from_db()
        assert record.email_type == et


class TestUnsubscribeEventFKProtection:
    def test_cannot_delete_subscriber_referenced_by_event(self) -> None:
        sub = Subscriber.objects.create(email="nullsub@example.com")
        UnsubscribeEvent.objects.create(
            subscriber=sub,
            email="nullsub@example.com",
            method="test",
        )
        with pytest.raises(ProtectedError):
            sub.delete()

    def test_subscriber_still_present_after_failed_delete(self) -> None:
        sub = Subscriber.objects.create(email="persist@example.com")
        event = UnsubscribeEvent.objects.create(
            subscriber=sub,
            email="persist@example.com",
            method="test",
        )
        with pytest.raises(ProtectedError):
            sub.delete()
        event.refresh_from_db()
        assert event.subscriber == sub

    def test_cannot_delete_email_type_referenced_by_event(self) -> None:
        et = EmailType.objects.create(slug="digest", name="Digest")
        UnsubscribeEvent.objects.create(
            email="etnull@example.com",
            email_type=et,
            method="test",
        )
        with pytest.raises(ProtectedError):
            et.delete()

    def test_email_type_still_present_after_failed_delete(self) -> None:
        et = EmailType.objects.create(slug="newsletter", name="Newsletter")
        event = UnsubscribeEvent.objects.create(
            email="etpersist@example.com",
            email_type=et,
            method="test",
        )
        with pytest.raises(ProtectedError):
            et.delete()
        event.refresh_from_db()
        assert event.email_type == et
