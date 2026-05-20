"""Verify that the Django project scaffolding starts without errors."""

from django.core.management import call_command


def test_django_check_passes() -> None:
    """``python manage.py check`` completes with no errors."""
    call_command("check", verbosity=0)


def test_root_urlconf_loads() -> None:
    """Root URL configuration resolves without import errors."""
    from django.urls import resolve

    match = resolve("/health/")
    assert match.func.__name__ == "health_check"
    assert match.func.__module__ == "apps.core.views"


def test_i18n_configured() -> None:
    """i18n settings match PLAN.md requirements."""
    from django.conf import settings

    assert settings.USE_I18N is True
    assert settings.LANGUAGE_CODE == "en"
    assert ("en", "English") in settings.LANGUAGES
    assert ("it", "Italiano") in settings.LANGUAGES
    assert settings.BASE_DIR / "locale" in settings.LOCALE_PATHS


def test_all_app_configs_registered() -> None:
    """All seven Postino apps are installed and importable."""
    from django.apps import apps

    expected = [
        "apps.core",
        "apps.subscribers",
        "apps.consent",
        "apps.campaigns",
        "apps.templates_mgr",
        "apps.analytics",
        "apps.webhooks",
    ]
    for app_label in expected:
        assert apps.is_installed(app_label), f"{app_label} is not installed"


def test_settings_mapped_from_config() -> None:
    """Django settings are mapped from the TOML config loader.

    pytest-django overrides DEBUG to False during test runs, so we skip
    that assertion. All other config-driven settings are verified here.
    """
    from django.conf import settings

    assert settings.SECRET_KEY == "test-secret-key-not-for-production-use-at-all"
    assert settings.TIME_ZONE == "UTC"
    assert "localhost" in settings.ALLOWED_HOSTS
    assert "127.0.0.1" in settings.ALLOWED_HOSTS
    assert "testserver" in settings.ALLOWED_HOSTS
    assert settings.DATABASES["default"]["ENGINE"] == "django.db.backends.sqlite3"


def test_postino_version_available() -> None:
    """POSTINO_VERSION is set in Django settings."""
    from django.conf import settings

    assert hasattr(settings, "POSTINO_VERSION")
    assert settings.POSTINO_VERSION


# ---------------------------------------------------------------------------
# Health endpoint tests
# ---------------------------------------------------------------------------


def test_health_endpoint_returns_200_json(client) -> None:  # noqa: ANN001
    """/health/ returns HTTP 200 with JSON content type."""
    response = client.get("/health/")
    assert response.status_code == 200
    assert response["Content-Type"] == "application/json"


def test_health_endpoint_payload(client) -> None:  # noqa: ANN001
    """/health/ JSON payload contains status=ok and a version field."""
    import json

    from django.conf import settings

    response = client.get("/health/")
    data = json.loads(response.content)
    assert data["status"] == "ok"
    assert "version" in data
    assert data["version"] == settings.POSTINO_VERSION


def test_health_endpoint_resolves_to_core_view() -> None:
    """/health/ resolves to apps.core.views.health_check, not an inline root function."""
    from django.urls import resolve

    match = resolve("/health/")
    assert match.namespace == "core"
    assert match.url_name == "health_check"
    assert match.func.__module__ == "apps.core.views"


# ---------------------------------------------------------------------------
# TimestampMixin tests
# ---------------------------------------------------------------------------


def test_timestamp_mixin_is_abstract() -> None:
    """TimestampMixin has Meta.abstract = True (no table created)."""
    from apps.core.models import TimestampMixin

    assert TimestampMixin._meta.abstract is True


def test_timestamp_mixin_has_created_at_field() -> None:
    """TimestampMixin provides a created_at DateTimeField with auto_now_add."""
    from apps.core.models import TimestampMixin

    field = TimestampMixin._meta.get_field("created_at")
    assert field.__class__.__name__ == "DateTimeField"
    assert field.auto_now_add is True


def test_timestamp_mixin_has_updated_at_field() -> None:
    """TimestampMixin provides an updated_at DateTimeField with auto_now."""
    from apps.core.models import TimestampMixin

    field = TimestampMixin._meta.get_field("updated_at")
    assert field.__class__.__name__ == "DateTimeField"
    assert field.auto_now is True
