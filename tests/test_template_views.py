"""Tests for template manager dashboard views: list, create, detail, edit.

Covers admin-only access, search filtering, empty state, pagination,
form validation, duplicate slug handling, preview rendering with
unsubscribe URL, and safe error display for invalid template syntax.
"""

import pytest
from django.contrib.auth.models import User
from django.test import Client

from apps.campaigns.models import Campaign
from apps.consent.models import EmailType
from apps.templates_mgr.models import EmailTemplate


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
def sample_template(db) -> EmailTemplate:
    return EmailTemplate.objects.create(
        name="Welcome Email",
        slug="welcome-email",
        subject_default="Welcome, {{ subscriber_name }}!",
        html_body="<h1>Hello {{ subscriber_name }}</h1><p>Glad to have you.</p>",
        text_body="Hello {{ subscriber_name }}\n\nGlad to have you.",
    )


@pytest.fixture
def sample_templates(db):
    """Create multiple templates for pagination and search tests."""
    templates = []
    for i in range(5):
        t = EmailTemplate.objects.create(
            name=f"Template {i}",
            slug=f"template-{i}",
            subject_default=f"Subject {i}",
            html_body=f"<p>Body {i}</p>",
        )
        templates.append(t)
    return templates


# ── Admin-only access ──────────────────────────────────────────────────


@pytest.mark.django_db
class TestTemplateViewAccess:
    def test_list_redirects_unauthenticated(self, client):
        response = client.get("/templates/")
        assert response.status_code == 302
        assert "/admin/login/" in response.url

    def test_list_forbids_non_staff(self, regular_client):
        response = regular_client.get("/templates/")
        assert response.status_code == 302
        assert "/admin/login/" in response.url

    def test_create_redirects_unauthenticated(self, client):
        response = client.get("/templates/new/")
        assert response.status_code == 302
        assert "/admin/login/" in response.url

    def test_detail_redirects_unauthenticated(self, client, sample_template):
        response = client.get(f"/templates/{sample_template.slug}/")
        assert response.status_code == 302
        assert "/admin/login/" in response.url

    def test_edit_redirects_unauthenticated(self, client, sample_template):
        response = client.get(f"/templates/{sample_template.slug}/edit/")
        assert response.status_code == 302
        assert "/admin/login/" in response.url


# ── Template list ──────────────────────────────────────────────────────


@pytest.mark.django_db
class TestTemplateList:
    def test_list_renders_templates(self, admin_client, sample_templates):
        response = admin_client.get("/templates/")
        assert response.status_code == 200
        content = response.content.decode()
        assert "Template 0" in content
        assert "Template 4" in content

    def test_list_shows_total_count(self, admin_client, sample_templates):
        response = admin_client.get("/templates/")
        assert response.status_code == 200
        assert b"5 template" in response.content or b"5 templates total" in response.content

    def test_list_search_by_name(self, admin_client, sample_templates):
        response = admin_client.get("/templates/?q=Template+3")
        assert response.status_code == 200
        content = response.content.decode()
        assert "Template 3" in content
        assert "Template 0" not in content

    def test_list_search_by_slug(self, admin_client, sample_templates):
        response = admin_client.get("/templates/?q=template-2")
        assert response.status_code == 200
        content = response.content.decode()
        assert "Template 2" in content

    def test_list_empty(self, admin_client, db):
        response = admin_client.get("/templates/")
        assert response.status_code == 200
        content = response.content.decode()
        assert "No templates found" in content

    def test_list_pagination(self, admin_client, db):
        for i in range(30):
            EmailTemplate.objects.create(
                name=f"Tmpl {i}",
                slug=f"tmpl-{i}",
                subject_default=f"Sub {i}",
                html_body=f"<p>{i}</p>",
            )
        response = admin_client.get("/templates/?page=2")
        assert response.status_code == 200
        assert response.context["page_obj"].number == 2

    def test_list_htmx_returns_partial_only(self, admin_client, sample_templates):
        response = admin_client.get(
            "/templates/", HTTP_HX_REQUEST="true"
        )
        assert response.status_code == 200
        content = response.content.decode()
        assert 'id="template-table"' in content
        assert "<html" not in content
        assert "Main navigation" not in content

    def test_list_shows_campaign_count(self, admin_client, sample_template, db):
        email_type = EmailType.objects.create(slug="newsletter", name="Newsletter")
        Campaign.objects.create(
            name="Camp A",
            email_type=email_type,
            template=sample_template,
            subject_line="Test",
            status=Campaign.Status.DRAFT,
        )
        Campaign.objects.create(
            name="Camp B",
            email_type=email_type,
            template=sample_template,
            subject_line="Test 2",
            status=Campaign.Status.DRAFT,
        )
        response = admin_client.get("/templates/")
        assert response.status_code == 200
        content = response.content.decode()
        # The template row should show campaign count
        assert "2" in content


# ── Template create ────────────────────────────────────────────────────


@pytest.mark.django_db
class TestTemplateCreate:
    def test_create_get_renders_form(self, admin_client, db):
        response = admin_client.get("/templates/new/")
        assert response.status_code == 200
        content = response.content.decode()
        assert "New Template" in content
        assert "name" in content.lower()

    def test_create_post_creates_template(self, admin_client, db):
        data = {
            "name": "My Template",
            "slug": "my-template",
            "subject_default": "Hello {{ subscriber_name }}",
            "html_body": "<p>Welcome!</p>",
            "text_body": "Welcome!",
        }
        response = admin_client.post("/templates/new/", data)
        assert response.status_code == 302
        tmpl = EmailTemplate.objects.get(slug="my-template")
        assert tmpl.name == "My Template"
        assert tmpl.subject_default == "Hello {{ subscriber_name }}"
        assert tmpl.text_body == "Welcome!"

    def test_create_duplicate_slug_rejected(self, admin_client, sample_template):
        data = {
            "name": "Duplicate",
            "slug": "welcome-email",
            "subject_default": "Another one",
            "html_body": "<p>Test</p>",
        }
        response = admin_client.post("/templates/new/", data)
        assert response.status_code == 200
        content = response.content.decode()
        assert "already exists" in content

    def test_create_missing_required_fields(self, admin_client, db):
        data = {
            "name": "",
            "slug": "",
            "subject_default": "",
            "html_body": "",
        }
        response = admin_client.post("/templates/new/", data)
        assert response.status_code == 200
        assert EmailTemplate.objects.count() == 0

    def test_create_minimal_valid_data(self, admin_client, db):
        data = {
            "name": "Minimal",
            "slug": "minimal",
            "subject_default": "Hi",
            "html_body": "Body",
        }
        response = admin_client.post("/templates/new/", data)
        assert response.status_code == 302
        assert EmailTemplate.objects.filter(slug="minimal").exists()


# ── Template detail ────────────────────────────────────────────────────


@pytest.mark.django_db
class TestTemplateDetail:
    def test_detail_shows_template(self, admin_client, sample_template):
        response = admin_client.get(f"/templates/{sample_template.slug}/")
        assert response.status_code == 200
        content = response.content.decode()
        assert "Welcome Email" in content
        assert "welcome-email" in content

    def test_detail_renders_preview_with_unsubscribe_url(self, admin_client, sample_template):
        response = admin_client.get(f"/templates/{sample_template.slug}/")
        assert response.status_code == 200
        preview = response.context["preview"]
        assert "error" not in preview
        assert preview["subject"] == "Welcome, Ada Lovelace!"
        assert "Ada Lovelace" in preview["html"]
        assert "preview-token" in preview["html"]
        assert "Unsubscribe" in preview["html"]

    def test_detail_shows_text_preview(self, admin_client, sample_template):
        response = admin_client.get(f"/templates/{sample_template.slug}/")
        preview = response.context["preview"]
        assert preview["text"]
        assert "Ada Lovelace" in preview["text"]

    def test_detail_invalid_syntax_shows_error(self, admin_client, db):
        tmpl = EmailTemplate.objects.create(
            name="Broken",
            slug="broken",
            subject_default="Hello",
            html_body="<p>{% invalid_tag %}</p>",
        )
        response = admin_client.get(f"/templates/{tmpl.slug}/")
        assert response.status_code == 200
        preview = response.context["preview"]
        assert "error" in preview
        assert preview["error"]
        content = response.content.decode()
        assert "Rendering error" in content

    def test_detail_nonexistent_template(self, admin_client, db):
        response = admin_client.get("/templates/no-such-slug/")
        assert response.status_code == 404

    def test_detail_shows_campaigns_using_template(self, admin_client, sample_template, db):
        email_type = EmailType.objects.create(slug="newsletter", name="Newsletter")
        Campaign.objects.create(
            name="Using This Template",
            email_type=email_type,
            template=sample_template,
            subject_line="Test",
            status=Campaign.Status.DRAFT,
        )
        response = admin_client.get(f"/templates/{sample_template.slug}/")
        content = response.content.decode()
        assert "Using This Template" in content
        assert "Campaigns using this template" in content

    def test_detail_shows_cli_hints(self, admin_client, sample_template):
        response = admin_client.get(f"/templates/{sample_template.slug}/")
        content = response.content.decode()
        assert f"postino templates get {sample_template.slug}" in content
        assert "postino templates list" in content
        # Nonexistent commands must not appear
        assert "postino templates render" not in content
        assert "postino templates test" not in content

    def test_detail_preview_context_renders_variables(self, admin_client, db):
        tmpl = EmailTemplate.objects.create(
            name="Vars",
            slug="vars",
            subject_default="Hello {{ subscriber_name }} — {{ current_date }}",
            html_body="<p>Email: {{ subscriber_email }}</p>",
        )
        response = admin_client.get(f"/templates/{tmpl.slug}/")
        preview = response.context["preview"]
        assert "Ada Lovelace" in preview["subject"]
        assert "2026-05-20" in preview["subject"]
        assert "ada@example.com" in preview["html"]


# ── Template edit ──────────────────────────────────────────────────────


@pytest.mark.django_db
class TestTemplateEdit:
    def test_edit_get_renders_form(self, admin_client, sample_template):
        response = admin_client.get(f"/templates/{sample_template.slug}/edit/")
        assert response.status_code == 200
        content = response.content.decode()
        assert "Edit Template" in content
        assert sample_template.name in content

    def test_edit_post_updates_template(self, admin_client, sample_template):
        data = {
            "name": "Updated Welcome",
            "slug": "welcome-email",
            "subject_default": "Hi {{ subscriber_name }}",
            "html_body": "<p>New body</p>",
            "text_body": "New text body",
        }
        response = admin_client.post(f"/templates/{sample_template.slug}/edit/", data)
        assert response.status_code == 302
        sample_template.refresh_from_db()
        assert sample_template.name == "Updated Welcome"
        assert sample_template.subject_default == "Hi {{ subscriber_name }}"
        assert sample_template.text_body == "New text body"

    def test_edit_same_slug_allowed(self, admin_client, sample_template):
        data = {
            "name": "Renamed Only",
            "slug": "welcome-email",
            "subject_default": sample_template.subject_default,
            "html_body": sample_template.html_body,
        }
        response = admin_client.post(f"/templates/{sample_template.slug}/edit/", data)
        assert response.status_code == 302
        sample_template.refresh_from_db()
        assert sample_template.name == "Renamed Only"

    def test_edit_duplicate_slug_rejected(self, admin_client, sample_template, db):
        EmailTemplate.objects.create(
            name="Other",
            slug="other-template",
            subject_default="Other",
            html_body="<p>Other</p>",
        )
        data = {
            "name": sample_template.name,
            "slug": "other-template",
            "subject_default": sample_template.subject_default,
            "html_body": sample_template.html_body,
        }
        response = admin_client.post(f"/templates/{sample_template.slug}/edit/", data)
        assert response.status_code == 200
        content = response.content.decode()
        assert "already exists" in content

    def test_edit_can_change_slug(self, admin_client, sample_template):
        data = {
            "name": sample_template.name,
            "slug": "new-slug",
            "subject_default": sample_template.subject_default,
            "html_body": sample_template.html_body,
        }
        response = admin_client.post(f"/templates/{sample_template.slug}/edit/", data)
        assert response.status_code == 302
        # Redirect goes to the new slug
        assert "/templates/new-slug/" in response.url
        assert EmailTemplate.objects.filter(slug="new-slug").exists()
        assert not EmailTemplate.objects.filter(slug="welcome-email").exists()

    def test_edit_nonexistent_template(self, admin_client, db):
        response = admin_client.get("/templates/no-such-slug/edit/")
        assert response.status_code == 404

    def test_edit_form_shows_slug_field(
        self, admin_client, sample_template
    ):
        response = admin_client.get(f"/templates/{sample_template.slug}/edit/")
        content = response.content.decode()
        assert 'name="slug"' in content
        assert sample_template.slug in content


# ── URL name resolution ────────────────────────────────────────────────


@pytest.mark.django_db
class TestTemplateURLNames:
    def test_list_url_name(self, admin_client, db):
        url = "/templates/"
        response = admin_client.get(url)
        assert response.status_code == 200

    def test_create_url_name(self, admin_client, db):
        url = "/templates/new/"
        response = admin_client.get(url)
        assert response.status_code == 200

    def test_detail_url_name(self, admin_client, sample_template):
        from django.urls import reverse
        url = reverse("templates_mgr:detail", kwargs={"slug": sample_template.slug})
        assert url == f"/templates/{sample_template.slug}/"
        response = admin_client.get(url)
        assert response.status_code == 200

    def test_edit_url_name(self, admin_client, sample_template):
        from django.urls import reverse
        url = reverse("templates_mgr:edit", kwargs={"slug": sample_template.slug})
        assert url == f"/templates/{sample_template.slug}/edit/"
        response = admin_client.get(url)
        assert response.status_code == 200
