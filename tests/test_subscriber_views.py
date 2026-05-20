"""Tests for subscriber dashboard views: list, detail, CSV import.

Covers admin-only access, filtering, pagination, HTMX partial rendering,
subscriber detail with consent/events, CSV import with suppression checks,
and re-subscription prevention.
"""

import io

import pytest
from django.contrib.auth.models import User
from django.test import Client

from apps.consent.models import UnsubscribeEvent
from apps.subscribers.models import Subscriber, Tag
from apps.subscribers.services import add_subscriber


@pytest.fixture
def client():
    return Client()


@pytest.fixture
def admin_client(client):
    User.objects.create_superuser(
        username="admin", email="admin@test.com", password="testpass123"
    )
    client.login(username="admin", password="testpass123")
    return client


@pytest.fixture
def regular_client(client):
    User.objects.create_user(
        username="regular", email="user@test.com", password="testpass123"
    )
    client.login(username="regular", password="testpass123")
    return client


@pytest.fixture
def sample_subscribers():
    """Create a set of subscribers with various statuses and tags."""
    sub_active = Subscriber.objects.create(
        email="alice@example.com", name="Alice", status="active",
        source="manual", health_score=80,
    )
    sub_pending = Subscriber.objects.create(
        email="bob@example.com", name="Bob", status="pending",
        source="import", health_score=50,
    )
    sub_unsub = Subscriber.objects.create(
        email="charlie@example.com", name="Charlie", status="unsubscribed",
        source="signup_form", health_score=20,
    )
    tag = Tag.objects.create(name="newsletter", display_name="Newsletter")
    sub_active.tags.add(tag)
    sub_pending.tags.add(tag)
    return sub_active, sub_pending, sub_unsub, tag


# ── Admin-only access ──────────────────────────────────────────────────


@pytest.mark.django_db
class TestSubscriberViewAccess:
    def test_list_redirects_unauthenticated(self, client):
        response = client.get("/subscribers/")
        assert response.status_code == 302
        assert "/admin/login/" in response.url

    def test_list_forbids_non_staff(self, regular_client):
        response = regular_client.get("/subscribers/")
        assert response.status_code == 302
        assert "/admin/login/" in response.url

    def test_detail_redirects_unauthenticated(self, client):
        sub = Subscriber.objects.create(email="test@example.com")
        response = client.get(f"/subscribers/{sub.id}/")
        assert response.status_code == 302
        assert "/admin/login/" in response.url

    def test_import_redirects_unauthenticated(self, client):
        response = client.get("/subscribers/import/")
        assert response.status_code == 302
        assert "/admin/login/" in response.url

    def test_import_post_redirects_unauthenticated(self, client):
        response = client.post("/subscribers/import/", {})
        assert response.status_code == 302
        assert "/admin/login/" in response.url


# ── Subscriber list view ───────────────────────────────────────────────


@pytest.mark.django_db
class TestSubscriberListView:
    def test_list_returns_200(self, admin_client, sample_subscribers):
        response = admin_client.get("/subscribers/")
        assert response.status_code == 200

    def test_list_uses_correct_template(self, admin_client, sample_subscribers):
        response = admin_client.get("/subscribers/")
        assert "subscribers/list.html" in [t.name for t in response.templates]

    def test_list_shows_subscribers(self, admin_client, sample_subscribers):
        response = admin_client.get("/subscribers/")
        content = response.content.decode()
        assert "alice@example.com" in content
        assert "bob@example.com" in content
        assert "charlie@example.com" in content

    def test_list_shows_total_count(self, admin_client, sample_subscribers):
        response = admin_client.get("/subscribers/")
        assert response.context["total_count"] == 3

    def test_list_has_nav_active(self, admin_client, sample_subscribers):
        response = admin_client.get("/subscribers/")
        assert response.context["nav_active"] == "subscribers"

    def test_filter_by_status(self, admin_client, sample_subscribers):
        response = admin_client.get("/subscribers/?status=active")
        assert response.status_code == 200
        emails = [s.email for s in response.context["subscribers"]]
        assert "alice@example.com" in emails
        assert "bob@example.com" not in emails
        assert "charlie@example.com" not in emails

    def test_filter_by_tag(self, admin_client, sample_subscribers):
        response = admin_client.get("/subscribers/?tag=newsletter")
        assert response.status_code == 200
        emails = [s.email for s in response.context["subscribers"]]
        assert "alice@example.com" in emails
        assert "bob@example.com" in emails
        assert "charlie@example.com" not in emails

    def test_filter_by_search_query_email(self, admin_client, sample_subscribers):
        response = admin_client.get("/subscribers/?q=alice")
        assert response.status_code == 200
        emails = [s.email for s in response.context["subscribers"]]
        assert "alice@example.com" in emails
        assert "bob@example.com" not in emails

    def test_filter_by_search_query_name(self, admin_client, sample_subscribers):
        response = admin_client.get("/subscribers/?q=Bob")
        assert response.status_code == 200
        emails = [s.email for s in response.context["subscribers"]]
        assert "bob@example.com" in emails
        assert "alice@example.com" not in emails

    def test_filter_by_health_below(self, admin_client, sample_subscribers):
        response = admin_client.get("/subscribers/?health_below=30")
        assert response.status_code == 200
        emails = [s.email for s in response.context["subscribers"]]
        assert "charlie@example.com" in emails
        assert "alice@example.com" not in emails

    def test_empty_state(self, admin_client):
        response = admin_client.get("/subscribers/")
        assert response.status_code == 200
        assert response.context["total_count"] == 0

    def test_pagination_default(self, admin_client):
        for i in range(30):
            Subscriber.objects.create(email=f"sub{i}@test.com", status="active")
        response = admin_client.get("/subscribers/")
        assert response.context["page_obj"].number == 1
        assert len(response.context["subscribers"]) == 25

    def test_pagination_page_2(self, admin_client):
        for i in range(30):
            Subscriber.objects.create(email=f"sub{i}@test.com", status="active")
        response = admin_client.get("/subscribers/?page=2")
        assert response.context["page_obj"].number == 2

    def test_htmx_returns_table_partial(self, admin_client, sample_subscribers):
        response = admin_client.get(
            "/subscribers/", HTTP_HX_REQUEST="true"
        )
        assert response.status_code == 200
        template_names = [t.name for t in response.templates]
        assert "subscribers/_table.html" in template_names
        assert "subscribers/list.html" not in template_names

    def test_htmx_filter_returns_partial(self, admin_client, sample_subscribers):
        response = admin_client.get(
            "/subscribers/?status=active", HTTP_HX_REQUEST="true"
        )
        assert "subscribers/_table.html" in [t.name for t in response.templates]


# ── Subscriber detail view ─────────────────────────────────────────────


@pytest.mark.django_db
class TestSubscriberDetailView:
    def test_detail_returns_200(self, admin_client, sample_subscribers):
        sub = sample_subscribers[0]
        response = admin_client.get(f"/subscribers/{sub.id}/")
        assert response.status_code == 200

    def test_detail_uses_correct_template(self, admin_client, sample_subscribers):
        sub = sample_subscribers[0]
        response = admin_client.get(f"/subscribers/{sub.id}/")
        assert "subscribers/detail.html" in [t.name for t in response.templates]

    def test_detail_shows_email(self, admin_client, sample_subscribers):
        sub = sample_subscribers[0]
        response = admin_client.get(f"/subscribers/{sub.id}/")
        content = response.content.decode()
        assert sub.email in content

    def test_detail_shows_name(self, admin_client, sample_subscribers):
        sub = sample_subscribers[0]
        response = admin_client.get(f"/subscribers/{sub.id}/")
        content = response.content.decode()
        assert sub.name in content

    def test_detail_shows_source(self, admin_client, sample_subscribers):
        sub = sample_subscribers[0]
        response = admin_client.get(f"/subscribers/{sub.id}/")
        content = response.content.decode()
        assert sub.source in content

    def test_detail_shows_health_score(self, admin_client, sample_subscribers):
        sub = sample_subscribers[0]
        response = admin_client.get(f"/subscribers/{sub.id}/")
        content = response.content.decode()
        assert str(sub.health_score) in content

    def test_detail_shows_tags(self, admin_client, sample_subscribers):
        sub = sample_subscribers[0]
        response = admin_client.get(f"/subscribers/{sub.id}/")
        content = response.content.decode()
        assert "Newsletter" in content

    def test_detail_shows_consent_records(self, admin_client):
        settings_overrides = {"POSTINO_REQUIRE_DOUBLE_OPTIN": False}
        from django.test import override_settings
        with override_settings(**settings_overrides):
            sub = add_subscriber("consent-test@example.com", name="Test", source="manual")
        response = admin_client.get(f"/subscribers/{sub.id}/")
        content = response.content.decode()
        assert "Grant" in content

    def test_detail_shows_unsubscribe_events(self, admin_client, sample_subscribers):
        sub = sample_subscribers[0]
        UnsubscribeEvent.objects.create(
            subscriber=sub,
            email=sub.email,
            method="test",
        )
        response = admin_client.get(f"/subscribers/{sub.id}/")
        content = response.content.decode()
        assert "test" in content

    def test_detail_404_for_missing(self, admin_client):
        import uuid
        response = admin_client.get(f"/subscribers/{uuid.uuid4()}/")
        assert response.status_code == 404

    def test_detail_has_cli_hint(self, admin_client, sample_subscribers):
        sub = sample_subscribers[0]
        response = admin_client.get(f"/subscribers/{sub.id}/")
        content = response.content.decode()
        assert "postino gdpr audit" in content

    def test_detail_shows_empty_consent(self, admin_client, sample_subscribers):
        sub = sample_subscribers[0]
        response = admin_client.get(f"/subscribers/{sub.id}/")
        content = response.content.decode()
        assert "No consent records" in content

    def test_detail_shows_empty_unsubscribe(self, admin_client, sample_subscribers):
        sub = sample_subscribers[0]
        response = admin_client.get(f"/subscribers/{sub.id}/")
        content = response.content.decode()
        assert "No unsubscribe events" in content


# ── CSV import view ────────────────────────────────────────────────────


def _make_csv(content: str) -> io.BytesIO:
    f = io.BytesIO(content.encode("utf-8"))
    f.name = "subscribers.csv"
    return f


@pytest.mark.django_db
class TestSubscriberImportView:
    def test_import_page_loads(self, admin_client):
        response = admin_client.get("/subscribers/import/")
        assert response.status_code == 200
        assert "subscribers/import.html" in [t.name for t in response.templates]

    def test_import_creates_subscribers(self, admin_client):
        csv_data = "email,name\nnew1@test.com,User One\nnew2@test.com,User Two\n"
        response = admin_client.post(
            "/subscribers/import/",
            {"csv_file": _make_csv(csv_data), "default_tag": ""},
            format="multipart",
        )
        assert response.status_code == 200
        assert response.context["import_results"]["created"] == 2
        assert Subscriber.objects.filter(email="new1@test.com").exists()
        assert Subscriber.objects.filter(email="new2@test.com").exists()

    def test_import_applies_default_tag(self, admin_client):
        csv_data = "email,name\ntagged@test.com,Tagged User\n"
        response = admin_client.post(
            "/subscribers/import/",
            {"csv_file": _make_csv(csv_data), "default_tag": "newsletter"},
            format="multipart",
        )
        assert response.status_code == 200
        sub = Subscriber.objects.get(email="tagged@test.com")
        tag_names = list(sub.tags.values_list("name", flat=True))
        assert "newsletter" in tag_names

    def test_import_applies_row_tag(self, admin_client):
        csv_data = "email,name,tag\nrowtag@test.com,Row Tag,premium\n"
        response = admin_client.post(
            "/subscribers/import/",
            {"csv_file": _make_csv(csv_data), "default_tag": ""},
            format="multipart",
        )
        assert response.status_code == 200
        sub = Subscriber.objects.get(email="rowtag@test.com")
        tag_names = list(sub.tags.values_list("name", flat=True))
        assert "premium" in tag_names

    def test_import_skips_suppressed_email(self, admin_client):
        UnsubscribeEvent.objects.create(
            email="suppressed@test.com",
            method="test",
        )
        csv_data = "email,name\nsuppressed@test.com,Suppressed\nnew@test.com,New\n"
        response = admin_client.post(
            "/subscribers/import/",
            {"csv_file": _make_csv(csv_data), "default_tag": ""},
            format="multipart",
        )
        assert response.status_code == 200
        results = response.context["import_results"]
        assert results["suppressed"] == 1
        assert results["created"] == 1
        assert not Subscriber.objects.filter(email="suppressed@test.com").exists()

    def test_import_no_re_subscription_of_unsubscribed(self, admin_client):
        sub = Subscriber.objects.create(
            email="unsubbed@test.com", status="unsubscribed", source="manual",
        )
        csv_data = "email,name\nunsubbed@test.com,Unsubbed\n"
        response = admin_client.post(
            "/subscribers/import/",
            {"csv_file": _make_csv(csv_data), "default_tag": ""},
            format="multipart",
        )
        assert response.status_code == 200
        results = response.context["import_results"]
        assert results["suppressed"] == 1
        sub.refresh_from_db()
        assert sub.status == "unsubscribed"

    def test_import_rejects_missing_email_column(self, admin_client):
        csv_data = "name,age\nAlice,30\n"
        response = admin_client.post(
            "/subscribers/import/",
            {"csv_file": _make_csv(csv_data), "default_tag": ""},
            format="multipart",
        )
        assert response.status_code == 200
        assert "import_results" not in response.context
        assert response.context["form"].errors

    def test_import_handles_empty_email_rows(self, admin_client):
        csv_data = "email,name\n,Empty Email\nvalid@test.com,Valid\n"
        response = admin_client.post(
            "/subscribers/import/",
            {"csv_file": _make_csv(csv_data), "default_tag": ""},
            format="multipart",
        )
        assert response.status_code == 200
        results = response.context["import_results"]
        assert results["created"] == 1
        assert len(results["errors"]) == 1

    def test_import_rejects_non_csv_file(self, admin_client):
        response = admin_client.post(
            "/subscribers/import/",
            {"csv_file": io.BytesIO(b"not a csv"), "default_tag": ""},
            format="multipart",
        )
        assert response.status_code == 200
        assert response.context["form"].errors

    def test_import_existing_subscriber_counts_skipped(self, admin_client):
        sub = Subscriber.objects.create(
            email="existing@test.com", name="Old Name", status="active", source="manual",
        )
        csv_data = "email,name\nexisting@test.com,New Name\n"
        response = admin_client.post(
            "/subscribers/import/",
            {"csv_file": _make_csv(csv_data), "default_tag": ""},
            format="multipart",
        )
        assert response.status_code == 200
        results = response.context["import_results"]
        assert results["created"] == 0
        assert results["skipped"] == 1
        sub.refresh_from_db()
        assert sub.name == "Old Name"
        assert Subscriber.objects.filter(email="existing@test.com").count() == 1

    def test_import_handles_bom(self, admin_client):
        csv_data = "\ufeffemail,name\nbom@test.com,BOM User\n"
        response = admin_client.post(
            "/subscribers/import/",
            {"csv_file": _make_csv(csv_data), "default_tag": ""},
            format="multipart",
        )
        assert response.status_code == 200
        results = response.context["import_results"]
        assert results["created"] == 1
        assert Subscriber.objects.filter(email="bom@test.com").exists()

    def test_import_get_returns_empty_form(self, admin_client):
        response = admin_client.get("/subscribers/import/")
        assert response.status_code == 200
        assert "form" in response.context
        assert "import_results" not in response.context

    def test_import_uses_add_subscriber_service(self, admin_client):
        csv_data = "email,name\nsvc@test.com,Service Test\n"
        response = admin_client.post(
            "/subscribers/import/",
            {"csv_file": _make_csv(csv_data), "default_tag": ""},
            format="multipart",
        )
        assert response.status_code == 200
        sub = Subscriber.objects.get(email="svc@test.com")
        assert sub.source == "import"

    def test_import_suppressed_historical_email_remains_suppressed(self, admin_client):
        UnsubscribeEvent.objects.create(
            email="hist-suppressed@test.com",
            method="manual",
        )
        csv_data = "email,name\nhist-suppressed@test.com,Historic\nnew@test.com,Fresh\n"
        response = admin_client.post(
            "/subscribers/import/",
            {"csv_file": _make_csv(csv_data), "default_tag": ""},
            format="multipart",
        )
        assert response.status_code == 200
        results = response.context["import_results"]
        assert results["suppressed"] == 1
        assert results["created"] == 1
        assert not Subscriber.objects.filter(email="hist-suppressed@test.com").exists()


# ── Pagination filter preservation ─────────────────────────────────────


@pytest.mark.django_db
class TestPaginationFilterPreservation:
    def _create_paginated_subscribers(self, admin_client, count=30):
        for i in range(count):
            Subscriber.objects.create(
                email=f"pag{i}@test.com", status="active", source="manual",
            )

    def test_full_page_pagination_preserves_search_filter(self, admin_client):
        self._create_paginated_subscribers(admin_client)
        response = admin_client.get("/subscribers/?q=pag&page=1")
        content = response.content.decode()
        assert "q=pag" in content

    def test_full_page_pagination_preserves_status_filter(self, admin_client):
        self._create_paginated_subscribers(admin_client)
        response = admin_client.get("/subscribers/?status=active&page=1")
        content = response.content.decode()
        assert "status=active" in content

    def test_full_page_pagination_preserves_tag_filter(self, admin_client):
        tag = Tag.objects.create(name="beta", display_name="Beta")
        for i in range(30):
            sub = Subscriber.objects.create(
                email=f"tagged{i}@test.com", status="active", source="manual",
            )
            sub.tags.add(tag)
        response = admin_client.get("/subscribers/?tag=beta&page=1")
        content = response.content.decode()
        assert "tag=beta" in content

    def test_full_page_pagination_preserves_multiple_filters(self, admin_client):
        self._create_paginated_subscribers(admin_client)
        response = admin_client.get("/subscribers/?q=pag&status=active&page=1")
        content = response.content.decode()
        assert "q=pag" in content
        assert "status=active" in content

    def test_htmx_partial_pagination_preserves_filters(self, admin_client):
        self._create_paginated_subscribers(admin_client)
        response = admin_client.get(
            "/subscribers/?q=pag&status=active",
            HTTP_HX_REQUEST="true",
        )
        content = response.content.decode()
        assert "q=pag" in content
        assert "status=active" in content

    def test_htmx_partial_pagination_preserves_tag_filter(self, admin_client):
        tag = Tag.objects.create(name="gamma", display_name="Gamma")
        for i in range(30):
            sub = Subscriber.objects.create(
                email=f"gt{i}@test.com", status="active", source="manual",
            )
            sub.tags.add(tag)
        response = admin_client.get(
            "/subscribers/?tag=gamma",
            HTTP_HX_REQUEST="true",
        )
        content = response.content.decode()
        assert "tag=gamma" in content
