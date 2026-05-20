"""Tests for the templates_mgr app.

Covers EmailTemplate model basics, admin registration, and the saved
template renderer (variable interpolation, subject rendering, HTML/text
output, and missing unsubscribe_url enforcement).
"""

from __future__ import annotations

import pytest
from django.contrib.admin import AdminSite

from apps.consent.email_renderer import MissingUnsubscribeURLError
from apps.templates_mgr.admin import EmailTemplateAdmin
from apps.templates_mgr.models import EmailTemplate
from apps.templates_mgr.renderer import render_saved_template

pytestmark = pytest.mark.django_db

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

UNSUBSCRIBE_URL = "https://testserver/unsubscribe/?token=abc123"


@pytest.fixture
def basic_template(db: None) -> EmailTemplate:
    return EmailTemplate.objects.create(
        name="Welcome",
        slug="welcome",
        subject_default="Welcome, {{ subscriber_name }}!",
        html_body="<p>Hello {{ subscriber_name }}, glad to have you!</p>",
        text_body="Hello {{ subscriber_name }}, glad to have you!",
    )


@pytest.fixture
def template_no_text(db: None) -> EmailTemplate:
    return EmailTemplate.objects.create(
        name="No Text Version",
        slug="no-text",
        subject_default="News from {{ app_name }}",
        html_body="<p>Check out our latest update.</p>",
        text_body="",
    )


@pytest.fixture
def base_ctx() -> dict:
    return {
        "unsubscribe_url": UNSUBSCRIBE_URL,
        "subscriber_name": "Ada",
    }


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------


class TestEmailTemplateModel:
    def test_create_and_str(self, basic_template: EmailTemplate) -> None:
        assert str(basic_template) == "Welcome (welcome)"

    def test_slug_is_unique(self, basic_template: EmailTemplate) -> None:
        from django.db import IntegrityError

        with pytest.raises(IntegrityError):
            EmailTemplate.objects.create(
                name="Duplicate",
                slug="welcome",
                subject_default="X",
                html_body="<p>X</p>",
            )

    def test_ordering_by_name(self, db: None) -> None:
        EmailTemplate.objects.create(
            name="Zebra", slug="zebra", subject_default="Z", html_body="Z",
        )
        EmailTemplate.objects.create(
            name="Alpha", slug="alpha", subject_default="A", html_body="A",
        )
        names = list(EmailTemplate.objects.values_list("name", flat=True))
        assert names == ["Alpha", "Zebra"]

    def test_timestamps_populated(self, basic_template: EmailTemplate) -> None:
        assert basic_template.created_at is not None
        assert basic_template.updated_at is not None

    def test_text_body_default_blank(self, db: None) -> None:
        t = EmailTemplate.objects.create(
            name="Minimal",
            slug="minimal",
            subject_default="Hi",
            html_body="<p>Hi</p>",
        )
        assert t.text_body == ""


# ---------------------------------------------------------------------------
# Admin
# ---------------------------------------------------------------------------


class TestEmailTemplateAdmin:
    def test_template_is_registered(self) -> None:
        from django.contrib import admin

        assert admin.site.is_registered(EmailTemplate)

    def test_list_display_fields(self) -> None:
        ma = EmailTemplateAdmin(EmailTemplate, AdminSite())
        assert "name" in ma.list_display
        assert "slug" in ma.list_display
        assert "subject_default" in ma.list_display

    def test_search_fields(self) -> None:
        ma = EmailTemplateAdmin(EmailTemplate, AdminSite())
        assert "name" in ma.search_fields
        assert "slug" in ma.search_fields

    def test_prepopulated_slug_from_name(self) -> None:
        ma = EmailTemplateAdmin(EmailTemplate, AdminSite())
        assert ma.prepopulated_fields == {"slug": ("name",)}

    def test_readonly_timestamps(self) -> None:
        ma = EmailTemplateAdmin(EmailTemplate, AdminSite())
        assert "created_at" in ma.readonly_fields
        assert "updated_at" in ma.readonly_fields


# ---------------------------------------------------------------------------
# Renderer — subject
# ---------------------------------------------------------------------------


class TestRenderSavedTemplateSubject:
    def test_subject_interpolates_variables(
        self, basic_template: EmailTemplate, base_ctx: dict,
    ) -> None:
        subject, _, _ = render_saved_template(basic_template, base_ctx)
        assert subject == "Welcome, Ada!"

    def test_subject_without_variables(self, base_ctx: dict, db: None) -> None:
        t = EmailTemplate.objects.create(
            name="Static", slug="static",
            subject_default="Plain subject", html_body="<p>X</p>",
        )
        subject, _, _ = render_saved_template(t, base_ctx)
        assert subject == "Plain subject"

    def test_subject_override_replaces_default(
        self, basic_template: EmailTemplate, base_ctx: dict,
    ) -> None:
        subject, _, _ = render_saved_template(
            basic_template, base_ctx, subject_override="Hey {{ subscriber_name }}!",
        )
        assert subject == "Hey Ada!"


# ---------------------------------------------------------------------------
# Renderer — HTML body
# ---------------------------------------------------------------------------


class TestRenderSavedTemplateHtml:
    def test_html_body_interpolates_variables(
        self, basic_template: EmailTemplate, base_ctx: dict,
    ) -> None:
        _, html, _ = render_saved_template(basic_template, base_ctx)
        assert "Hello Ada, glad to have you!" in html

    def test_html_contains_unsubscribe_link(
        self, basic_template: EmailTemplate, base_ctx: dict,
    ) -> None:
        _, html, _ = render_saved_template(basic_template, base_ctx)
        assert UNSUBSCRIBE_URL in html
        assert "Unsubscribe" in html

    def test_html_contains_app_name(
        self, basic_template: EmailTemplate, base_ctx: dict,
    ) -> None:
        _, html, _ = render_saved_template(basic_template, base_ctx)
        assert "Postino Test" in html

    def test_html_includes_base_layout(
        self, basic_template: EmailTemplate, base_ctx: dict,
    ) -> None:
        _, html, _ = render_saved_template(basic_template, base_ctx)
        assert "<!DOCTYPE html>" in html
        assert '<table role="presentation"' in html


# ---------------------------------------------------------------------------
# Renderer — text body
# ---------------------------------------------------------------------------


class TestRenderSavedTemplateText:
    def test_text_body_interpolates_variables(
        self, basic_template: EmailTemplate, base_ctx: dict,
    ) -> None:
        _, _, text = render_saved_template(basic_template, base_ctx)
        assert "Hello Ada, glad to have you!" in text

    def test_text_contains_unsubscribe_link(
        self, basic_template: EmailTemplate, base_ctx: dict,
    ) -> None:
        _, _, text = render_saved_template(basic_template, base_ctx)
        assert UNSUBSCRIBE_URL in text

    def test_text_empty_when_template_has_no_text(
        self, template_no_text: EmailTemplate, base_ctx: dict,
    ) -> None:
        _, _, text = render_saved_template(template_no_text, base_ctx)
        assert "Check out our latest update." not in text

    def test_text_includes_greeting(
        self, basic_template: EmailTemplate, base_ctx: dict,
    ) -> None:
        _, _, text = render_saved_template(basic_template, base_ctx)
        assert "Hi Ada" in text


# ---------------------------------------------------------------------------
# Renderer — unsubscribe URL enforcement
# ---------------------------------------------------------------------------


class TestRenderSavedTemplateUnsubscribeEnforcement:
    def test_raises_missing_unsubscribe_url(
        self, basic_template: EmailTemplate,
    ) -> None:
        with pytest.raises(MissingUnsubscribeURLError):
            render_saved_template(basic_template, {"subscriber_name": "Ada"})

    def test_raises_blank_unsubscribe_url(
        self, basic_template: EmailTemplate,
    ) -> None:
        with pytest.raises(MissingUnsubscribeURLError):
            render_saved_template(
                basic_template,
                {"subscriber_name": "Ada", "unsubscribe_url": "   "},
            )


# ---------------------------------------------------------------------------
# Renderer — error handling
# ---------------------------------------------------------------------------


class TestRenderSavedTemplateErrors:
    def test_missing_variable_renders_as_empty(
        self, base_ctx: dict, db: None,
    ) -> None:
        t = EmailTemplate.objects.create(
            name="Missing Var", slug="missing-var",
            subject_default="Hi {{ nonexistent_var }}",
            html_body="<p>{{ nonexistent_var }}</p>",
        )
        subject, html, _ = render_saved_template(t, base_ctx)
        assert subject == "Hi "
        assert "<p></p>" in html

    def test_returns_triple_of_strings(
        self, basic_template: EmailTemplate, base_ctx: dict,
    ) -> None:
        result = render_saved_template(basic_template, base_ctx)
        assert isinstance(result, tuple)
        assert len(result) == 3
        assert all(isinstance(s, str) for s in result)
