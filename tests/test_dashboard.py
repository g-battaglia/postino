"""Tests for dashboard base layout, sidebar, components, and chart template tags.

Phase 3 block 1: templates, HTMX setup, SVG charts, shared components.
"""

import pytest
from django.contrib.auth.models import User
from django.template import Context, Template
from django.test import Client, RequestFactory


@pytest.fixture
def client():
    return Client()


@pytest.fixture
def authenticated_client(client):
    User.objects.create_superuser(
        username="admin", email="admin@test.com", password="testpass123"
    )
    client.login(username="admin", password="testpass123")
    return client


@pytest.fixture
def factory():
    return RequestFactory()


# ── Dashboard view ────────────────────────────────────────────────────


@pytest.mark.django_db
class TestDashboardView:
    def test_dashboard_returns_200_for_authenticated_user(self, authenticated_client):
        response = authenticated_client.get("/")
        assert response.status_code == 200

    def test_dashboard_uses_correct_template(self, authenticated_client):
        response = authenticated_client.get("/")
        assert "dashboard/index.html" in [t.name for t in response.templates]

    def test_dashboard_includes_nav_context(self, authenticated_client):
        response = authenticated_client.get("/")
        assert response.context["nav_active"] == "dashboard"

    def test_dashboard_redirects_unauthenticated(self, client):
        response = client.get("/")
        assert response.status_code == 302
        assert "/admin/login/" in response.url

    def test_dashboard_forbids_non_staff(self, client):
        from django.contrib.auth.models import User
        User.objects.create_user(
            username="regular", email="user@test.com", password="testpass123"
        )
        client.login(username="regular", password="testpass123")
        response = client.get("/")
        assert response.status_code == 302
        assert "/admin/login/" in response.url


# ── Base template ─────────────────────────────────────────────────────


@pytest.mark.django_db
class TestBaseTemplate:
    def test_base_renders_htmx_script(self, authenticated_client):
        response = authenticated_client.get("/")
        content = response.content.decode()
        assert 'src="/static/js/htmx.min.js"' in content

    def test_base_renders_tailwind_cdn(self, authenticated_client):
        response = authenticated_client.get("/")
        content = response.content.decode()
        assert "cdn.tailwindcss.com" in content

    def test_base_renders_sidebar(self, authenticated_client):
        response = authenticated_client.get("/")
        content = response.content.decode()
        assert "Dashboard" in content
        assert "Subscribers" in content
        assert "Campaigns" in content
        assert "Templates" in content
        assert "Analytics" in content
        assert "Settings" in content

    def test_base_has_skip_link(self, authenticated_client):
        response = authenticated_client.get("/")
        content = response.content.decode()
        assert "Skip to content" in content

    def test_base_has_landmark_roles(self, authenticated_client):
        response = authenticated_client.get("/")
        content = response.content.decode()
        assert 'role="navigation"' in content
        assert 'role="main"' in content

    def test_base_has_content_block(self, authenticated_client):
        response = authenticated_client.get("/")
        content = response.content.decode()
        assert "main-content" in content

    def test_base_has_language_attr(self, authenticated_client):
        response = authenticated_client.get("/")
        content = response.content.decode()
        assert 'lang="en"' in content

    def test_base_no_js_frameworks(self, authenticated_client):
        response = authenticated_client.get("/")
        content = response.content.decode()
        assert "alpine" not in content.lower()
        assert "react" not in content.lower()
        assert "vue" not in content.lower()


# ── Component templates ───────────────────────────────────────────────


@pytest.mark.django_db
class TestStatusBadgeComponent:
    def _render(self, status: str) -> str:
        template = Template(
            '{% load i18n %}{% include "components/status_badge.html" with status=status_var %}'
        )
        return template.render(Context({"status_var": status}))

    def test_active_badge(self):
        output = self._render("active")
        assert "Active" in output
        assert "bg-emerald-50" in output

    def test_bounced_badge(self):
        output = self._render("bounced")
        assert "Bounced" in output
        assert "bg-red-50" in output

    def test_pending_badge(self):
        output = self._render("pending")
        assert "Pending" in output
        assert "bg-amber-50" in output

    def test_draft_badge(self):
        output = self._render("draft")
        assert "Draft" in output
        assert "bg-gray-100" in output

    def test_unknown_status_falls_through(self):
        output = self._render("unknown_status")
        assert "bg-gray-100" in output
        assert "Unknown_Status" in output

    def test_badge_is_rounded_pill(self):
        output = self._render("active")
        assert "rounded-full" in output


@pytest.mark.django_db
class TestStatCardComponent:
    def _render(self, **ctx) -> str:
        template = Template(
            '{% load i18n %}'
            '{% include "components/stat_card.html"'
            ' with label=label value=value trend=trend meta=meta %}'
        )
        return template.render(Context(ctx))

    def test_renders_label_and_value(self):
        output = self._render(label="Active subscribers", value="4,127")
        assert "Active subscribers" in output
        assert "4,127" in output

    def test_renders_trend_up(self):
        output = self._render(label="Test", value="100", trend="+8.4")
        assert "8.4%" in output
        assert "text-emerald-500" in output

    def test_renders_trend_down(self):
        output = self._render(label="Test", value="100", trend="-0.4")
        assert "0.4%" in output
        assert "text-red-500" in output


@pytest.mark.django_db
class TestEmptyStateComponent:
    def _render(self, **ctx) -> str:
        template = Template(
            '{% load i18n %}{% include "components/empty_state.html" %}'
        )
        return template.render(Context(ctx))

    def test_renders_default_message(self):
        output = self._render()
        assert "Nothing here yet" in output

    def test_renders_custom_title(self):
        output = self._render(title="No subscribers yet")
        assert "No subscribers yet" in output

    def test_renders_action_button(self):
        output = self._render(
            title="Empty",
            action_url="/subscribers/import/",
            action_label="Import CSV",
        )
        assert "Import CSV" in output
        assert '/subscribers/import/"' in output


@pytest.mark.django_db
class TestHealthBarComponent:
    def _render(self, score: int) -> str:
        template = Template(
            '{% include "components/health_bar.html" with score=score_var %}'
        )
        return template.render(Context({"score_var": score}))

    def test_healthy_score_uses_green(self):
        output = self._render(85)
        assert "#10b981" in output
        assert "85" in output

    def test_at_risk_score_uses_amber(self):
        output = self._render(50)
        assert "#f59e0b" in output

    def test_critical_score_uses_red(self):
        output = self._render(25)
        assert "#ef4444" in output


@pytest.mark.django_db
class TestTagComponent:
    def _render(self, **ctx) -> str:
        template = Template(
            '{% include "components/tag.html" %}'
        )
        return template.render(Context(ctx))

    def test_renders_tag_name(self):
        output = self._render(name="paid", color="green")
        assert "paid" in output
        assert "bg-emerald-100" in output

    def test_renders_removable_button(self):
        output = self._render(name="test", removable=True)
        assert "&times;" in output


# ── SVG Chart template tags ───────────────────────────────────────────


@pytest.mark.django_db
class TestChartSparkline:
    def _render(self, **ctx) -> str:
        template = Template(
            "{% load postino_charts %}{% chart_sparkline data color=color %}"
        )
        return template.render(Context(ctx))

    def test_outputs_svg(self):
        output = self._render(data="10,20,30,40,50", color="#6366f1")
        assert "<svg" in output
        assert "</svg>" in output

    def test_includes_stroke_color(self):
        output = self._render(data="10,20,30", color="#10b981")
        assert "#10b981" in output

    def test_empty_data_returns_empty(self):
        output = self._render(data="42", color="#000")
        assert output.strip() == ""

    def test_uses_list_data(self):
        output = self._render(data=[10, 20, 30, 40, 50], color="#000")
        assert "<svg" in output


@pytest.mark.django_db
class TestChartGrowth:
    def _render(self, **ctx) -> str:
        template = Template(
            "{% load postino_charts %}{% chart_growth data %}"
        )
        return template.render(Context(ctx))

    def test_outputs_svg_with_list_data(self):
        data = [
            {"month": "Jan", "new": 420, "churned": 80},
            {"month": "Feb", "new": 500, "churned": 60},
        ]
        output = self._render(data=data)
        assert "<svg" in output
        assert "Jan" in output
        assert "#10b981" in output  # green area
        assert "#ef4444" in output  # red area

    def test_empty_data_returns_no_data_message(self):
        output = self._render(data=[])
        assert "No data" in output

    def test_comma_separated_string_parsing(self):
        output = self._render(data="420,80,500,60")
        assert "<svg" in output

    def test_sanitizes_month_labels(self):
        data = [
            {"month": "<script>alert(1)</script>", "new": 10, "churned": 1},
            {"month": "Feb", "new": 20, "churned": 2},
        ]
        output = self._render(data=data)
        assert "<script>" not in output


@pytest.mark.django_db
class TestChartBar:
    def _render(self, **ctx) -> str:
        template = Template(
            "{% load postino_charts %}{% chart_bar data %}"
        )
        return template.render(Context(ctx))

    def test_outputs_svg_with_data(self):
        data = [
            {"label": "1d", "delivered": 120, "bounced": 3, "other": 0},
            {"label": "2d", "delivered": 100, "bounced": 5, "other": 1},
        ]
        output = self._render(data=data)
        assert "<svg" in output
        assert "#10b981" in output
        assert "#ef4444" in output

    def test_empty_data(self):
        output = self._render(data=[])
        assert "No data" in output


@pytest.mark.django_db
class TestChartProgressRing:
    def _render(self, **ctx) -> str:
        template = Template(
            "{% load postino_charts %}{% chart_progress_ring score label=label %}"
        )
        return template.render(Context(ctx))

    def test_outputs_ring_svg(self):
        output = self._render(score=74, label="Health")
        assert "<svg" in output
        assert "74" in output
        assert "Health" in output

    def test_healthy_color(self):
        output = self._render(score=85)
        assert "#10b981" in output

    def test_at_risk_color(self):
        output = self._render(score=50)
        assert "#f59e0b" in output

    def test_critical_color(self):
        output = self._render(score=25)
        assert "#ef4444" in output

    def test_custom_size(self):
        output = self._render(score=50)
        assert "relative" in output

    def test_escapes_label(self):
        output = self._render(score=50, label="<script>")
        assert "<script>" not in output


# ── Safe URL template tag ─────────────────────────────────────────────


@pytest.mark.django_db
class TestSafeUrlTag:
    def _render(self, url_name: str) -> str:
        template = Template(
            "{% load postino_utils %}{% safe_url name %}"
        )
        return template.render(Context({"name": url_name}))

    def test_resolves_existing_url(self):
        output = self._render("core:dashboard")
        assert output == "/"

    def test_resolves_health_check(self):
        output = self._render("core:health_check")
        assert output == "/health/"

    def test_returns_hash_for_missing_url(self):
        output = self._render("nonexistent:view")
        assert output == "#"


# ── HTMX static file ──────────────────────────────────────────────────


@pytest.mark.django_db
class TestHtmxSetup:
    def test_htmx_file_exists_in_static(self):
        from pathlib import Path

        static_path = Path(__file__).resolve().parent.parent / "static" / "js" / "htmx.min.js"
        assert static_path.exists()
        content = static_path.read_text()
        assert "htmx" in content
        assert len(content) > 1000
