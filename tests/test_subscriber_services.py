"""Tests for subscriber service functions.

Covers add_subscriber, get_subscriber, list_subscribers, count_subscribers,
export_subscriber_data, tag_subscriber, and suppress_subscriber.
"""

from __future__ import annotations

import pytest

from apps.consent.models import ConsentRecord, UnsubscribeEvent
from apps.subscribers.models import Subscriber, Tag
from apps.subscribers.services import (
    SuppressedSubscriberError,
    add_subscriber,
    count_subscribers,
    export_subscriber_data,
    get_subscriber,
    list_subscribers,
    suppress_subscriber,
    tag_subscriber,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tag_newsletter(db) -> Tag:
    return Tag.objects.create(name="newsletter", display_name="Newsletter")


@pytest.fixture
def active_subscriber(db) -> Subscriber:
    return Subscriber.objects.create(
        email="alice@example.com",
        name="Alice",
        status=Subscriber.Status.ACTIVE,
        source=Subscriber.Source.MANUAL,
    )


@pytest.fixture
def suppressed_subscriber(db) -> Subscriber:
    return Subscriber.objects.create(
        email="bob@example.com",
        name="Bob",
        status=Subscriber.Status.UNSUBSCRIBED,
        source=Subscriber.Source.MANUAL,
    )


# ---------------------------------------------------------------------------
# add_subscriber
# ---------------------------------------------------------------------------


class TestAddSubscriber:
    def test_add_subscriber_creates_pending_with_double_optin(self, db) -> None:
        subscriber = add_subscriber("carol@example.com", name="Carol")

        assert subscriber.status == Subscriber.Status.PENDING
        assert subscriber.double_optin_token is not None
        assert len(subscriber.double_optin_token) > 0

    def test_add_subscriber_creates_active_without_double_optin(
        self, db, settings
    ) -> None:
        settings.POSTINO_REQUIRE_DOUBLE_OPTIN = False

        subscriber = add_subscriber("dave@example.com", name="Dave")

        assert subscriber.status == Subscriber.Status.ACTIVE
        assert subscriber.double_optin_token is None

    def test_add_subscriber_with_double_optin_creates_no_consent_grant(self, db) -> None:
        subscriber = add_subscriber("eve@example.com", name="Eve")

        assert subscriber.status == Subscriber.Status.PENDING
        assert ConsentRecord.objects.filter(subscriber=subscriber).count() == 0

    def test_add_subscriber_without_double_optin_creates_consent_grant(
        self, db, settings
    ) -> None:
        settings.POSTINO_REQUIRE_DOUBLE_OPTIN = False

        subscriber = add_subscriber("eve2@example.com", name="Eve2")

        assert subscriber.status == Subscriber.Status.ACTIVE
        records = ConsentRecord.objects.filter(subscriber=subscriber)
        assert records.count() == 1
        record = records.first()
        assert record.action == ConsentRecord.Action.GRANT
        assert record.method == "manual"

    def test_add_subscriber_with_tags(self, db, tag_newsletter) -> None:
        subscriber = add_subscriber(
            "frank@example.com", name="Frank", tag_names=["newsletter"]
        )

        tag_names = list(subscriber.tags.values_list("name", flat=True))
        assert tag_names == ["newsletter"]

    def test_add_subscriber_with_nonexistent_tag_is_ignored(self, db) -> None:
        subscriber = add_subscriber(
            "grace@example.com", name="Grace", tag_names=["nonexistent"]
        )

        assert subscriber.tags.count() == 0

    def test_add_subscriber_rejects_suppressed_email(self, suppressed_subscriber) -> None:
        with pytest.raises(SuppressedSubscriberError):
            add_subscriber("bob@example.com")

    def test_add_subscriber_rejects_email_in_unsubscribe_event(self, db) -> None:
        UnsubscribeEvent.objects.create(
            subscriber=None,
            email="historically_suppressed@example.com",
            method="link",
        )

        with pytest.raises(SuppressedSubscriberError):
            add_subscriber("historically_suppressed@example.com")

    def test_add_subscriber_returns_existing_if_active(self, active_subscriber) -> None:
        result = add_subscriber("alice@example.com")

        assert result.id == active_subscriber.id
        assert result.email == "alice@example.com"

    def test_add_subscriber_normalizes_email(self, db) -> None:
        subscriber = add_subscriber("  Upper@Example.COM  ")

        assert subscriber.email == "upper@example.com"

    def test_add_subscriber_stores_ip_address(self, db) -> None:
        subscriber = add_subscriber("henry@example.com", ip_address="192.168.1.1")

        assert str(subscriber.ip_address) == "192.168.1.1"

    def test_add_subscriber_stores_metadata(self, db) -> None:
        meta = {"plan": "pro", "signup_page": "/pricing"}
        subscriber = add_subscriber("irene@example.com", metadata=meta)

        assert subscriber.metadata == meta

    def test_add_subscriber_default_source_is_manual(self, db) -> None:
        subscriber = add_subscriber("jack@example.com")

        assert subscriber.source == Subscriber.Source.MANUAL

    def test_add_subscriber_with_custom_source(self, db) -> None:
        subscriber = add_subscriber("kate@example.com", source="import", source_id="ext-42")

        assert subscriber.source == "import"
        assert subscriber.source_id == "ext-42"

    def test_add_subscriber_consent_record_uses_source_as_method(
        self, db, settings
    ) -> None:
        settings.POSTINO_REQUIRE_DOUBLE_OPTIN = False
        add_subscriber("leo@example.com", source="signup_form")

        record = ConsentRecord.objects.filter(
            subscriber__email="leo@example.com"
        ).first()
        assert record.method == "signup_form"


# ---------------------------------------------------------------------------
# get_subscriber
# ---------------------------------------------------------------------------


class TestGetSubscriber:
    def test_get_subscriber_by_email(self, active_subscriber) -> None:
        result = get_subscriber("alice@example.com")

        assert result.id == active_subscriber.id

    def test_get_subscriber_by_uuid(self, active_subscriber) -> None:
        result = get_subscriber(str(active_subscriber.id))

        assert result.id == active_subscriber.id

    def test_get_subscriber_raises_on_missing(self, db) -> None:
        with pytest.raises(Subscriber.DoesNotExist):
            get_subscriber("nobody@example.com")

    def test_get_subscriber_normalizes_email(self, active_subscriber) -> None:
        result = get_subscriber("  Alice@Example.COM  ")

        assert result.id == active_subscriber.id


# ---------------------------------------------------------------------------
# list_subscribers
# ---------------------------------------------------------------------------


class TestListSubscribers:
    def test_returns_all_with_no_filters(self, db) -> None:
        Subscriber.objects.create(email="a@test.com", status=Subscriber.Status.ACTIVE)
        Subscriber.objects.create(email="b@test.com", status=Subscriber.Status.ACTIVE)

        subscribers, total = list_subscribers()
        assert total == 2
        assert len(subscribers) == 2

    def test_list_subscribers_with_status_filter(self, db) -> None:
        Subscriber.objects.create(email="a@test.com", status=Subscriber.Status.ACTIVE)
        Subscriber.objects.create(email="b@test.com", status=Subscriber.Status.PENDING)

        subscribers, total = list_subscribers(status=Subscriber.Status.ACTIVE)
        assert total == 1
        assert subscribers[0].status == Subscriber.Status.ACTIVE

    def test_list_subscribers_with_tag_filter(self, db, tag_newsletter) -> None:
        s1 = Subscriber.objects.create(
            email="a@test.com", status=Subscriber.Status.ACTIVE
        )
        Subscriber.objects.create(
            email="b@test.com", status=Subscriber.Status.ACTIVE
        )
        s1.tags.add(tag_newsletter)

        subscribers, total = list_subscribers(tag="newsletter")
        assert total == 1
        assert subscribers[0].id == s1.id

    def test_list_subscribers_with_health_below(self, db) -> None:
        Subscriber.objects.create(
            email="a@test.com",
            health_score=20,
            status=Subscriber.Status.ACTIVE,
        )
        Subscriber.objects.create(
            email="b@test.com",
            health_score=80,
            status=Subscriber.Status.ACTIVE,
        )

        subscribers, total = list_subscribers(health_below=50)
        assert total == 1
        assert subscribers[0].health_score == 20

    def test_list_subscribers_pagination(self, db) -> None:
        for i in range(5):
            Subscriber.objects.create(email=f"s{i}@test.com", status=Subscriber.Status.ACTIVE)

        page1, total = list_subscribers(limit=2, offset=0)
        page2, _ = list_subscribers(limit=2, offset=2)

        assert total == 5
        assert len(page1) == 2
        assert len(page2) == 2

    def test_list_subscribers_empty_result(self, db) -> None:
        subscribers, total = list_subscribers()
        assert total == 0
        assert subscribers == []


# ---------------------------------------------------------------------------
# count_subscribers
# ---------------------------------------------------------------------------


class TestCountSubscribers:
    def test_count_all(self, db) -> None:
        Subscriber.objects.create(email="a@test.com", status=Subscriber.Status.ACTIVE)
        Subscriber.objects.create(email="b@test.com", status=Subscriber.Status.PENDING)

        assert count_subscribers() == 2

    def test_count_with_status_filter(self, db) -> None:
        Subscriber.objects.create(email="a@test.com", status=Subscriber.Status.ACTIVE)
        Subscriber.objects.create(email="b@test.com", status=Subscriber.Status.PENDING)

        assert count_subscribers(status=Subscriber.Status.ACTIVE) == 1

    def test_count_with_tag_filter(self, db, tag_newsletter) -> None:
        s = Subscriber.objects.create(email="a@test.com", status=Subscriber.Status.ACTIVE)
        s.tags.add(tag_newsletter)
        Subscriber.objects.create(email="b@test.com", status=Subscriber.Status.ACTIVE)

        assert count_subscribers(tag="newsletter") == 1

    def test_count_empty(self, db) -> None:
        assert count_subscribers() == 0


# ---------------------------------------------------------------------------
# export_subscriber_data
# ---------------------------------------------------------------------------


class TestExportSubscriberData:
    def test_export_subscriber_data_includes_all_related(self, db, tag_newsletter) -> None:
        subscriber = Subscriber.objects.create(
            email="export@test.com",
            name="Export Test",
            status=Subscriber.Status.ACTIVE,
            source="manual",
            metadata={"plan": "pro"},
        )
        subscriber.tags.add(tag_newsletter)

        ConsentRecord.objects.create(
            subscriber=subscriber,
            email_type=None,
            action=ConsentRecord.Action.GRANT,
            method="manual",
        )

        data = export_subscriber_data(subscriber)

        assert data["subscriber"]["email"] == "export@test.com"
        assert data["subscriber"]["name"] == "Export Test"
        assert data["subscriber"]["status"] == "active"
        assert data["subscriber"]["metadata"] == {"plan": "pro"}
        assert data["subscriber"]["id"] == str(subscriber.id)

        assert len(data["consent_records"]) == 1
        assert data["consent_records"][0]["action"] == "grant"

        assert "newsletter" in data["tags"]

        assert isinstance(data["unsubscribe_events"], list)
        assert len(data["unsubscribe_events"]) == 0

    def test_export_includes_unsubscribe_events(self, active_subscriber) -> None:
        UnsubscribeEvent.objects.create(
            subscriber=active_subscriber,
            email=active_subscriber.email,
            method="link",
        )

        data = export_subscriber_data(active_subscriber)

        assert len(data["unsubscribe_events"]) == 1
        assert data["unsubscribe_events"][0]["email"] == "alice@example.com"

    def test_export_dates_are_iso_strings(self, active_subscriber) -> None:
        data = export_subscriber_data(active_subscriber)

        assert "T" in data["subscriber"]["created_at"]
        assert "T" in data["subscriber"]["updated_at"]


# ---------------------------------------------------------------------------
# tag_subscriber
# ---------------------------------------------------------------------------


class TestTagSubscriber:
    def test_tag_subscriber_adds_tag(self, active_subscriber, tag_newsletter) -> None:
        tag_subscriber(active_subscriber, "newsletter")

        assert active_subscriber.tags.filter(name="newsletter").exists()

    def test_tag_subscriber_removes_tag(self, active_subscriber, tag_newsletter) -> None:
        active_subscriber.tags.add(tag_newsletter)

        tag_subscriber(active_subscriber, "newsletter", remove=True)

        assert not active_subscriber.tags.filter(name="newsletter").exists()

    def test_tag_subscriber_creates_tag_if_missing(self, active_subscriber) -> None:
        assert not Tag.objects.filter(name="new-tag").exists()

        tag_subscriber(active_subscriber, "new-tag")

        assert Tag.objects.filter(name="new-tag").exists()
        assert active_subscriber.tags.filter(name="new-tag").exists()

    def test_tag_subscriber_sets_display_name_on_create(self, active_subscriber) -> None:
        tag_subscriber(active_subscriber, "my_newsletter")

        tag = Tag.objects.get(name="my_newsletter")
        assert tag.display_name == "My Newsletter"

    def test_tag_subscriber_idempotent_add(self, active_subscriber, tag_newsletter) -> None:
        tag_subscriber(active_subscriber, "newsletter")
        tag_subscriber(active_subscriber, "newsletter")

        assert active_subscriber.tags.filter(name="newsletter").count() == 1

    def test_tag_subscriber_idempotent_remove(self, active_subscriber) -> None:
        tag_subscriber(active_subscriber, "newsletter", remove=True)


# ---------------------------------------------------------------------------
# suppress_subscriber
# ---------------------------------------------------------------------------


class TestSuppressSubscriber:
    def test_suppress_subscriber(self, active_subscriber) -> None:
        event = suppress_subscriber(active_subscriber, reason="manual")

        active_subscriber.refresh_from_db()
        assert active_subscriber.status == Subscriber.Status.UNSUBSCRIBED
        assert isinstance(event, UnsubscribeEvent)
        assert event.email == "alice@example.com"
        assert event.method == "manual"

    def test_suppress_subscriber_creates_unsubscribe_event(self, active_subscriber) -> None:
        suppress_subscriber(active_subscriber, reason="admin_action")

        events = UnsubscribeEvent.objects.filter(subscriber=active_subscriber)
        assert events.count() == 1
        assert events.first().method == "admin_action"

    def test_suppress_subscriber_idempotent(self, suppressed_subscriber) -> None:
        event = suppress_subscriber(suppressed_subscriber, reason="manual")

        suppressed_subscriber.refresh_from_db()
        assert suppressed_subscriber.status == Subscriber.Status.UNSUBSCRIBED
        assert isinstance(event, UnsubscribeEvent)
