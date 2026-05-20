"""Tests for the email template renderer.

Validates that render_email correctly merges context, resolves templates,
and produces both HTML and plain-text output.
"""

from __future__ import annotations

import pytest
from django.template.loader import TemplateDoesNotExist

from apps.consent.email_renderer import (
    MissingUnsubscribeURLError,
    _build_base_context,
    render_email,
)

# ---------------------------------------------------------------------------
# _build_base_context
# ---------------------------------------------------------------------------


class TestBuildBaseContext:
    def test_returns_defaults_when_context_empty(self):
        ctx = _build_base_context({})
        assert ctx["app_name"] == "Postino Test"
        assert ctx["primary_color"] == "#6366f1"
        assert ctx["subscriber_name"] == ""
        assert ctx["unsubscribe_url"] == ""

    def test_caller_values_override_defaults(self):
        ctx = _build_base_context({"app_name": "My App", "subscriber_name": "Ada"})
        assert ctx["app_name"] == "My App"
        assert ctx["subscriber_name"] == "Ada"
        # Non-overridden defaults remain
        assert ctx["primary_color"] == "#6366f1"

    def test_physical_address_from_settings(self):
        ctx = _build_base_context({})
        assert "physical_address" in ctx


# ---------------------------------------------------------------------------
# render_email
# ---------------------------------------------------------------------------


class TestRenderEmail:
    @pytest.fixture
    def base_context(self) -> dict:
        return {
            "unsubscribe_url": "https://testserver/unsubscribe/?token=abc",
            "subscriber_name": "Ada",
        }

    def test_renders_base_html_template(self, base_context):
        html, text = render_email("emails/base_email.html", base_context)
        assert "Ada" in html
        assert "Unsubscribe" in html
        assert "https://testserver/unsubscribe/?token=abc" in html

    def test_renders_base_text_template(self, base_context):
        html, text = render_email("emails/base_email.html", base_context)
        assert "Ada" in text
        assert "https://testserver/unsubscribe/?token=abc" in text
        assert "Unsubscribe" in text

    def test_html_contains_table_layout(self, base_context):
        html, _ = render_email("emails/base_email.html", base_context)
        assert '<table role="presentation"' in html

    def test_html_contains_primary_color(self, base_context):
        html, _ = render_email("emails/base_email.html", base_context)
        assert "#6366f1" in html

    def test_html_contains_app_name(self, base_context):
        html, _ = render_email("emails/base_email.html", base_context)
        assert "Postino Test" in html

    def test_footer_shows_unsubscribe_reason(self, base_context):
        html, _ = render_email("emails/base_email.html", base_context)
        assert "subscribed to Postino Test" in html

    def test_text_footer_shows_unsubscribe_reason(self, base_context):
        _, text = render_email("emails/base_email.html", base_context)
        assert "subscribed to Postino Test" in text

    def test_physical_address_shown_when_present(self, base_context):
        base_context["physical_address"] = "123 Via Roma, Roma, Italy"
        html, text = render_email("emails/base_email.html", base_context)
        assert "123 Via Roma, Roma, Italy" in html
        assert "123 Via Roma, Roma, Italy" in text

    def test_no_physical_address_no_render(self, base_context):
        html, _ = render_email("emails/base_email.html", base_context)
        # Default test settings have empty physical_address
        assert "123 Via" not in html

    def test_raises_for_missing_template(self):
        with pytest.raises(TemplateDoesNotExist):
            render_email("emails/nonexistent.html", {
                "unsubscribe_url": "https://testserver/unsubscribe/?token=x",
            })

    def test_raises_for_missing_unsubscribe_url(self):
        with pytest.raises(MissingUnsubscribeURLError, match="unsubscribe_url"):
            render_email("emails/base_email.html", {})

    def test_raises_for_blank_unsubscribe_url(self):
        with pytest.raises(MissingUnsubscribeURLError, match="unsubscribe_url"):
            render_email("emails/base_email.html", {"unsubscribe_url": "   "})

    def test_handles_empty_subscriber_name(self):
        html, text = render_email("emails/base_email.html", {
            "unsubscribe_url": "https://testserver/unsubscribe/?token=x",
            "subscriber_name": "",
        })
        # Should not contain "Hi ," — the greeting block is conditional
        assert "Hi ," not in html
        assert "Hi ," not in text

    def test_returns_tuple_of_two_strings(self, base_context):
        result = render_email("emails/base_email.html", base_context)
        assert isinstance(result, tuple)
        assert len(result) == 2
        assert isinstance(result[0], str)
        assert isinstance(result[1], str)
