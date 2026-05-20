"""Tests for Phase 6: sync models, sync service, auto-tagging, bulk actions.

Covers DataSource and SyncLog models, sync_data_source service,
evaluate_auto_tag, bulk action views, and management command.
"""

from __future__ import annotations

import json
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
from django.test import Client

from apps.consent.models import ConsentRecord, UnsubscribeEvent
from apps.subscribers.models import DataSource, Subscriber, SyncLog, Tag
from apps.subscribers.services import (
    evaluate_auto_tag,
    sync_data_source,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def staff_user(db, django_user_model):
    user = django_user_model.objects.create_superuser(
        username="admin", email="admin@test.com", password="pass"
    )
    return user


@pytest.fixture
def staff_client(staff_user):
    client = Client()
    client.login(username="admin", password="pass")
    return client


@pytest.fixture
def tag_pro(db) -> Tag:
    return Tag.objects.create(
        name="pro",
        display_name="Pro",
        auto_rule={"metadata.plan": "pro"},
    )


@pytest.fixture
def tag_active(db) -> Tag:
    return Tag.objects.create(
        name="active-users",
        display_name="Active Users",
        auto_rule={"status": "active"},
    )


@pytest.fixture
def tag_no_rule(db) -> Tag:
    return Tag.objects.create(name="newsletter", display_name="Newsletter", auto_rule=None)


@pytest.fixture
def active_subscriber(db) -> Subscriber:
    return Subscriber.objects.create(
        email="alice@example.com",
        name="Alice",
        status=Subscriber.Status.ACTIVE,
        source=Subscriber.Source.MANUAL,
        metadata={"plan": "pro"},
    )


@pytest.fixture
def pending_subscriber(db) -> Subscriber:
    return Subscriber.objects.create(
        email="bob@example.com",
        name="Bob",
        status=Subscriber.Status.PENDING,
        source=Subscriber.Source.MANUAL,
        metadata={},
    )


@pytest.fixture
def suppressed_subscriber(db) -> Subscriber:
    return Subscriber.objects.create(
        email="charlie@example.com",
        name="Charlie",
        status=Subscriber.Status.UNSUBSCRIBED,
        source=Subscriber.Source.MANUAL,
    )


@pytest.fixture
def sqlite_db_path():
    """Create a temporary SQLite database with test data for sync."""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    conn = sqlite3.connect(tmp.name)
    cur = conn.cursor()
    cur.execute(
        "CREATE TABLE users (id TEXT, email TEXT, first_name TEXT, plan TEXT)"
    )
    cur.execute(
        "INSERT INTO users VALUES ('1', 'new@example.com', 'NewUser', 'free')"
    )
    cur.execute(
        "INSERT INTO users VALUES ('2', 'existing@example.com', 'Exists', 'pro')"
    )
    conn.commit()
    conn.close()
    yield tmp.name
    Path(tmp.name).unlink(missing_ok=True)


@pytest.fixture
def data_source(db, sqlite_db_path) -> DataSource:
    return DataSource.objects.create(
        name="Test Source",
        source_type=DataSource.SourceType.DATABASE,
        config={
            "database_url": f"sqlite:///{sqlite_db_path}",
            "query": "SELECT id, email, first_name, plan FROM users",
            "field_map": {"email": "email", "name": "first_name", "source_id": "id"},
            "tag": "synced",
            "metadata_fields": ["plan"],
        },
        is_active=True,
    )


# ---------------------------------------------------------------------------
# DataSource / SyncLog model tests
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestDataSourceModel:
    def test_create_data_source(self, db):
        ds = DataSource.objects.create(
            name="My Source",
            source_type=DataSource.SourceType.DATABASE,
            config={"database_url": "sqlite:///test.db"},
        )
        assert ds.pk is not None
        assert str(ds) == "My Source"
        assert ds.is_active is True
        assert ds.sync_interval_hours == 6

    def test_data_source_default_tag(self, db):
        tag = Tag.objects.create(name="default", display_name="Default")
        ds = DataSource.objects.create(
            name="Tagged Source",
            source_type=DataSource.SourceType.DATABASE,
            config={},
            default_tag=tag,
        )
        assert ds.default_tag == tag


@pytest.mark.django_db
class TestSyncLogModel:
    def test_create_sync_log(self, db):
        from django.utils import timezone

        ds = DataSource.objects.create(name="Log Test", source_type="database", config={})
        log = SyncLog.objects.create(
            data_source=ds,
            status=SyncLog.Status.SUCCESS,
            new_count=5,
            updated_count=3,
            started_at=timezone.now(),
        )
        assert log.pk is not None
        assert log.new_count == 5
        assert "Log Test" in str(log)


# ---------------------------------------------------------------------------
# Auto-tagging tests
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestAutoTagging:
    def test_rule_matches_metadata(self, active_subscriber, tag_pro):
        applied = evaluate_auto_tag(active_subscriber)
        assert tag_pro in applied
        assert active_subscriber.tags.filter(pk=tag_pro.pk).exists()

    def test_rule_matches_status(self, active_subscriber, tag_active):
        applied = evaluate_auto_tag(active_subscriber)
        assert tag_active in applied

    def test_no_match_for_wrong_metadata(self, pending_subscriber, tag_pro):
        applied = evaluate_auto_tag(pending_subscriber)
        assert tag_pro not in applied

    def test_no_duplicate_tagging(self, active_subscriber, tag_pro):
        evaluate_auto_tag(active_subscriber)
        applied = evaluate_auto_tag(active_subscriber)
        assert tag_pro not in applied

    def test_tags_without_rules_are_ignored(self, active_subscriber, tag_no_rule):
        applied = evaluate_auto_tag(active_subscriber)
        assert tag_no_rule not in applied

    def test_auto_tag_wired_into_add_subscriber(self, db, tag_pro):
        from django.conf import settings

        with patch.object(settings, "POSTINO_REQUIRE_DOUBLE_OPTIN", False):
            from apps.subscribers.services import add_subscriber

            sub = add_subscriber(
                "auto@example.com",
                metadata={"plan": "pro"},
            )
            assert sub.tags.filter(pk=tag_pro.pk).exists()


# ---------------------------------------------------------------------------
# Sync service tests
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestSyncService:
    def test_sync_creates_new_subscribers(self, data_source):
        Subscriber.objects.create(
            email="existing@example.com",
            name="Old",
            status=Subscriber.Status.ACTIVE,
        )
        result = sync_data_source(data_source)
        assert result.new_count >= 1
        assert result.source_name == "Test Source"

    def test_sync_updates_existing_subscribers(self, data_source):
        Subscriber.objects.create(
            email="existing@example.com",
            name="Old",
            status=Subscriber.Status.ACTIVE,
        )
        result = sync_data_source(data_source)
        assert result.updated_count >= 1

    def test_sync_skips_suppressed_by_unsubscribe_event(self, data_source):
        sub = Subscriber.objects.create(
            email="new@example.com",
            name="Unsubbed",
            status=Subscriber.Status.ACTIVE,
        )
        UnsubscribeEvent.objects.create(
            subscriber=sub,
            email="new@example.com",
            method="test",
        )
        data_source.config["query"] = "SELECT id, email, first_name, plan FROM users"
        data_source.save()
        result = sync_data_source(data_source)
        assert result.suppressed_count >= 1

    def test_sync_skips_suppressed_subscriber(self, data_source):
        Subscriber.objects.create(
            email="new@example.com",
            name="Suppressed",
            status=Subscriber.Status.UNSUBSCRIBED,
        )
        result = sync_data_source(data_source)
        assert result.suppressed_count >= 1

    def test_sync_dry_run_does_not_create(self, data_source):
        result = sync_data_source(data_source, dry_run=True)
        assert result.new_count >= 1
        assert not Subscriber.objects.filter(email="new@example.com").exists()

    def test_sync_missing_config_returns_error(self, db):
        ds = DataSource.objects.create(name="Empty", source_type="database", config={})
        result = sync_data_source(ds)
        assert len(result.errors) >= 1
        assert "Missing" in result.errors[0]

    def test_sync_applies_default_tag(self, data_source):
        sync_data_source(data_source)
        new_sub = Subscriber.objects.filter(email="new@example.com").first()
        if new_sub:
            assert new_sub.tags.filter(name="synced").exists()

    def test_sync_applies_model_default_tag(self, data_source):
        tag = Tag.objects.create(name="crm", display_name="CRM")
        data_source.default_tag = tag
        data_source.config["tag"] = ""
        data_source.save()

        sync_data_source(data_source)
        new_sub = Subscriber.objects.filter(email="new@example.com").first()

        assert new_sub is not None
        assert new_sub.tags.filter(name="crm").exists()

    def test_sync_applies_tags_to_existing_subscribers(self, data_source):
        existing = Subscriber.objects.create(
            email="existing@example.com",
            name="Old",
            status=Subscriber.Status.ACTIVE,
        )

        sync_data_source(data_source)
        existing.refresh_from_db()

        assert existing.tags.filter(name="synced").exists()

    def test_sync_applies_auto_tags_to_existing_subscribers(self, data_source, tag_pro):
        existing = Subscriber.objects.create(
            email="existing@example.com",
            name="Old",
            status=Subscriber.Status.ACTIVE,
            metadata={},
        )

        sync_data_source(data_source)
        existing.refresh_from_db()

        assert existing.tags.filter(pk=tag_pro.pk).exists()

    def test_sync_creates_consent_record(self, data_source):
        sync_data_source(data_source)
        new_sub = Subscriber.objects.filter(email="new@example.com").first()
        if new_sub:
            assert ConsentRecord.objects.filter(subscriber=new_sub, method="sync").exists()


# ---------------------------------------------------------------------------
# Management command tests
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestSyncSourceCommand:
    def test_command_no_active_sources(self, db):
        from io import StringIO

        from django.core.management import call_command

        out = StringIO()
        call_command("sync_source", stdout=out)
        assert "No active data sources" in out.getvalue()

    def test_command_syncs_source(self, data_source):
        from io import StringIO

        from django.core.management import call_command

        out = StringIO()
        call_command("sync_source", stdout=out)
        assert "Syncing 'Test Source'" in out.getvalue()
        assert SyncLog.objects.filter(data_source=data_source).exists()

    def test_command_dry_run(self, data_source):
        from io import StringIO

        from django.core.management import call_command

        out = StringIO()
        call_command("sync_source", dry_run=True, stdout=out)
        assert "(dry run)" in out.getvalue()
        log = SyncLog.objects.first()
        assert log.status == SyncLog.Status.DRY_RUN

    def test_command_filter_by_name(self, data_source):
        from io import StringIO

        from django.core.management import call_command

        out = StringIO()
        call_command("sync_source", source="Test Source", stdout=out)
        assert "Syncing 'Test Source'" in out.getvalue()

    def test_command_updates_last_sync_at(self, data_source):
        from django.core.management import call_command

        call_command("sync_source")
        data_source.refresh_from_db()
        assert data_source.last_sync_at is not None


# ---------------------------------------------------------------------------
# CLI sync tests
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestCLISync:
    def test_sync_sources_json(self, data_source):
        from click.testing import CliRunner

        from cli.cli import main

        runner = CliRunner()
        result = runner.invoke(main, ["sync", "sources", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["ok"] is True

    def test_sync_status_json(self, db):
        from click.testing import CliRunner

        from cli.cli import main

        runner = CliRunner()
        result = runner.invoke(main, ["sync", "status", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["ok"] is True

    def test_sync_run(self, data_source):
        from click.testing import CliRunner

        from cli.cli import main

        runner = CliRunner()
        result = runner.invoke(main, ["sync", "run", "--json"])
        assert result.exit_code == 0


# ---------------------------------------------------------------------------
# Bulk action tests
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestBulkTagView:
    def test_bulk_tag_subscribers(self, staff_client, active_subscriber, pending_subscriber):
        response = staff_client.post(
            "/subscribers/bulk/tag/",
            {
                "tag": "vip",
                "subscriber_ids": json.dumps([str(active_subscriber.id)]),
            },
        )
        assert response.status_code == 302
        assert active_subscriber.tags.filter(name="vip").exists()

    def test_bulk_tag_accepts_selected_list(self, staff_client, active_subscriber):
        response = staff_client.post(
            "/subscribers/bulk/tag/",
            {
                "tag": "vip",
                "selected": [str(active_subscriber.id)],
            },
        )
        assert response.status_code == 302
        assert active_subscriber.tags.filter(name="vip").exists()

    def test_bulk_tag_skips_suppressed(self, staff_client, suppressed_subscriber):
        response = staff_client.post(
            "/subscribers/bulk/tag/",
            {
                "tag": "vip",
                "subscriber_ids": json.dumps([str(suppressed_subscriber.id)]),
            },
        )
        assert response.status_code == 302
        assert not suppressed_subscriber.tags.filter(name="vip").exists()

    def test_bulk_tag_requires_login(self, db):
        client = Client()
        response = client.post("/subscribers/bulk/tag/", {"tag": "test", "subscriber_ids": "[]"})
        assert response.status_code != 200


@pytest.mark.django_db
class TestBulkSuppressView:
    def test_bulk_suppress_subscribers(self, staff_client, active_subscriber):
        response = staff_client.post(
            "/subscribers/bulk/suppress/",
            {
                "reason": "bulk_test",
                "subscriber_ids": json.dumps([str(active_subscriber.id)]),
            },
        )
        assert response.status_code == 302
        active_subscriber.refresh_from_db()
        assert active_subscriber.is_suppressed
        assert UnsubscribeEvent.objects.filter(email=active_subscriber.email).exists()

    def test_bulk_suppress_enforces_invariant(self, staff_client, suppressed_subscriber):
        response = staff_client.post(
            "/subscribers/bulk/suppress/",
            {
                "reason": "already",
                "subscriber_ids": json.dumps([str(suppressed_subscriber.id)]),
            },
        )
        assert response.status_code == 302
        suppressed_subscriber.refresh_from_db()
        assert suppressed_subscriber.status == Subscriber.Status.UNSUBSCRIBED


@pytest.mark.django_db
class TestBulkExportView:
    def test_bulk_export_csv(self, staff_client, active_subscriber):
        response = staff_client.post(
            "/subscribers/bulk/export/",
            {"subscriber_ids": json.dumps([str(active_subscriber.id)])},
        )
        assert response.status_code == 200
        assert response["Content-Type"] == "text/csv"
        content = response.content.decode()
        assert "alice@example.com" in content

    def test_bulk_export_get(self, staff_client, active_subscriber):
        response = staff_client.get(
            "/subscribers/bulk/export/",
            {"subscriber_ids": json.dumps([str(active_subscriber.id)])},
        )
        assert response.status_code == 200

    def test_bulk_export_accepts_selected_list(self, staff_client, active_subscriber):
        response = staff_client.post(
            "/subscribers/bulk/export/",
            {"selected": [str(active_subscriber.id)]},
        )
        assert response.status_code == 200
        assert "alice@example.com" in response.content.decode()


@pytest.mark.django_db
class TestSubscriberListBulkUI:
    def test_list_renders_bulk_actions(self, staff_client, active_subscriber):
        response = staff_client.get("/subscribers/")
        content = response.content.decode()
        assert response.status_code == 200
        assert "Bulk actions" in content
        assert 'name="selected"' in content
        assert "/subscribers/bulk/tag/" in content


# ---------------------------------------------------------------------------
# Settings view tests
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestSettingsView:
    def test_settings_page_loads(self, staff_client):
        response = staff_client.get("/settings/")
        assert response.status_code == 200
        assert b"Settings" in response.content

    def test_settings_general_tab_shows_config(self, staff_client):
        response = staff_client.get("/settings/")
        content = response.content.decode()
        assert "Postino" in content
        assert "console" in content

    def test_settings_types_tab(self, staff_client):
        response = staff_client.get("/settings/?tab=types")
        assert response.status_code == 200
        content = response.content.decode()
        assert "email types" in content.lower()

    def test_settings_requires_login(self, db):
        client = Client()
        response = client.get("/settings/")
        assert response.status_code != 200
