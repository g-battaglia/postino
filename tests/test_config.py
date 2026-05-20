"""Tests for postino.config: TOML loader, Pydantic validation, env overrides, redaction."""

from __future__ import annotations

import textwrap
from pathlib import Path
from typing import Any

import pytest

from cli.config import (
    ConfigError,
    PostinoConfig,
    _parse_env_value,
    load_config,
    redact_config,
)


def _write_config(tmp_path: Path, toml_content: str) -> Path:
    p = tmp_path / "config.toml"
    p.write_text(textwrap.dedent(toml_content))
    return p


_MINIMAL_VALID = """
    [server]
    secret_key = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    debug = true
    allowed_hosts = ["localhost"]
    timezone = "UTC"
    base_url = "http://localhost:8000"

    [database]
    url = "sqlite:///test.sqlite3"

    [email]
    provider = "console"
    from_name = "Test"
    from_email = "test@example.com"

    [security]
    unsubscribe_secret = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"

    [gdpr]

    [branding]

    [sentry]
"""


class TestLoadConfig:
    def test_valid_config_loads(self, tmp_path: Path) -> None:
        path = _write_config(tmp_path, _MINIMAL_VALID)
        cfg = load_config(path)
        assert cfg.server.secret_key == "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
        assert cfg.server.debug is True
        assert cfg.email.provider == "console"
        assert cfg.database.url == "sqlite:///test.sqlite3"
        assert cfg.security.unsubscribe_secret == "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"

    def test_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(ConfigError, match="not found"):
            load_config(tmp_path / "nonexistent.toml")

    def test_invalid_toml_raises(self, tmp_path: Path) -> None:
        path = _write_config(tmp_path, "this is [not valid {{{ toml")
        with pytest.raises(ConfigError, match="Invalid TOML"):
            load_config(path)

    def test_postino_config_env_var(self, tmp_path: Path, monkeypatch: Any) -> None:
        path = _write_config(tmp_path, _MINIMAL_VALID)
        monkeypatch.setenv("POSTINO_CONFIG", str(path))
        cfg = load_config()
        assert cfg.server.debug is True


class TestValidationRules:
    def test_empty_secret_key_fails(self, tmp_path: Path) -> None:
        toml = _MINIMAL_VALID.replace(
            'secret_key = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"',
            'secret_key = ""',
        )
        path = _write_config(tmp_path, toml)
        with pytest.raises(ConfigError, match="secret_key must not be empty"):
            load_config(path)

    def test_short_unsubscribe_secret_fails(self, tmp_path: Path) -> None:
        toml = _MINIMAL_VALID.replace(
            "unsubscribe_secret = \"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\"",
            'unsubscribe_secret = "short"',
        )
        path = _write_config(tmp_path, toml)
        with pytest.raises(ConfigError, match="at least 32 characters"):
            load_config(path)

    def test_unknown_provider_fails(self, tmp_path: Path) -> None:
        toml = _MINIMAL_VALID.replace(
            'provider = "console"', 'provider = "sendgrid"'
        )
        path = _write_config(tmp_path, toml)
        with pytest.raises(ConfigError, match="email.provider must be one of"):
            load_config(path)

    def test_empty_database_url_fails(self, tmp_path: Path) -> None:
        toml = _MINIMAL_VALID.replace(
            'url = "sqlite:///test.sqlite3"', 'url = ""'
        )
        path = _write_config(tmp_path, toml)
        with pytest.raises(ConfigError, match="database.url must not be empty"):
            load_config(path)

    def test_debug_false_with_console_fails(self, tmp_path: Path) -> None:
        toml = _MINIMAL_VALID.replace(
            "debug = true", "debug = false"
        ).replace(
            'provider = "console"', 'provider = "resend"'
        )
        toml = toml.replace('provider = "resend"', 'provider = "console"')
        path = _write_config(tmp_path, toml)
        with pytest.raises(ConfigError, match="cannot be.*console.*debug"):
            load_config(path)

    def test_debug_false_with_resend_succeeds(self, tmp_path: Path) -> None:
        toml = _MINIMAL_VALID.replace("debug = true", "debug = false").replace(
            'provider = "console"', 'provider = "resend"'
        )
        path = _write_config(tmp_path, toml)
        cfg = load_config(path)
        assert cfg.server.debug is False
        assert cfg.email.provider == "resend"

    def test_empty_base_url_fails(self, tmp_path: Path) -> None:
        toml = _MINIMAL_VALID.replace(
            'base_url = "http://localhost:8000"', 'base_url = ""'
        )
        path = _write_config(tmp_path, toml)
        with pytest.raises(ConfigError, match="base_url must not be empty"):
            load_config(path)


class TestEnvOverrides:
    def test_simple_override(self, tmp_path: Path, monkeypatch: Any) -> None:
        path = _write_config(tmp_path, _MINIMAL_VALID)
        monkeypatch.setenv("POSTINO_SERVER__DEBUG", "false")
        monkeypatch.setenv("POSTINO_EMAIL__PROVIDER", "resend")
        cfg = load_config(path)
        assert cfg.server.debug is False
        assert cfg.email.provider == "resend"

    def test_boolean_parsing(self, tmp_path: Path, monkeypatch: Any) -> None:
        path = _write_config(tmp_path, _MINIMAL_VALID)
        monkeypatch.setenv("POSTINO_SERVER__DEBUG", "false")
        monkeypatch.setenv("POSTINO_EMAIL__PROVIDER", "resend")
        cfg = load_config(path)
        assert cfg.server.debug is False
        assert cfg.email.provider == "resend"

    def test_integer_parsing(self, tmp_path: Path, monkeypatch: Any) -> None:
        path = _write_config(tmp_path, _MINIMAL_VALID)
        monkeypatch.setenv("POSTINO_GDPR__UNSUBSCRIBED_RETENTION_DAYS", "42")
        cfg = load_config(path)
        assert cfg.gdpr.unsubscribed_retention_days == 42

    def test_float_parsing(self, tmp_path: Path, monkeypatch: Any) -> None:
        path = _write_config(tmp_path, _MINIMAL_VALID)
        monkeypatch.setenv("POSTINO_SENTRY__TRACES_SAMPLE_RATE", "0.5")
        cfg = load_config(path)
        assert cfg.sentry.traces_sample_rate == 0.5

    def test_comma_separated_list(self, tmp_path: Path, monkeypatch: Any) -> None:
        path = _write_config(tmp_path, _MINIMAL_VALID)
        monkeypatch.setenv("POSTINO_SERVER__ALLOWED_HOSTS", "example.com,api.example.com")
        cfg = load_config(path)
        assert cfg.server.allowed_hosts == ["example.com", "api.example.com"]

    def test_nested_section_override(self, tmp_path: Path, monkeypatch: Any) -> None:
        path = _write_config(tmp_path, _MINIMAL_VALID)
        monkeypatch.setenv("POSTINO_EMAIL__RESEND__API_KEY", "re_test_123")
        cfg = load_config(path)
        assert cfg.email.resend.api_key == "re_test_123"

    def test_env_overrides_toml(self, tmp_path: Path, monkeypatch: Any) -> None:
        path = _write_config(tmp_path, _MINIMAL_VALID)
        monkeypatch.setenv("POSTINO_SERVER__SECRET_KEY", "env-override-key-that-is-long-enough")
        cfg = load_config(path)
        assert cfg.server.secret_key == "env-override-key-that-is-long-enough"


class TestParseEnvValue:
    def test_true_variants(self) -> None:
        for val in ("1", "true", "True", "TRUE", "yes", "on"):
            assert _parse_env_value(val) is True

    def test_false_variants(self) -> None:
        for val in ("0", "false", "False", "FALSE", "no", "off"):
            assert _parse_env_value(val) is False

    def test_integer(self) -> None:
        assert _parse_env_value("42") == 42
        assert _parse_env_value("0") is False  # "0" matches false first

    def test_float(self) -> None:
        result = _parse_env_value("3.14")
        assert result == 3.14

    def test_comma_list(self) -> None:
        assert _parse_env_value("a,b,c") == ["a", "b", "c"]

    def test_plain_string(self) -> None:
        assert _parse_env_value("hello") == "hello"


class TestRedaction:
    def test_secret_key_redacted(self) -> None:
        data = {"server": {"secret_key": "sensitive-value"}}
        result = redact_config(data)
        assert result["server"]["secret_key"] == "***REDACTED***"

    def test_empty_secret_not_redacted(self) -> None:
        data = {"server": {"secret_key": ""}}
        result = redact_config(data)
        assert result["server"]["secret_key"] == ""

    def test_api_key_redacted(self) -> None:
        data = {"email": {"resend": {"api_key": "re_secret"}}}
        result = redact_config(data)
        assert result["email"]["resend"]["api_key"] == "***REDACTED***"

    def test_non_secret_preserved(self) -> None:
        data = {"server": {"debug": True, "timezone": "UTC"}}
        result = redact_config(data)
        assert result["server"]["debug"] is True
        assert result["server"]["timezone"] == "UTC"

    def test_database_url_redacted(self) -> None:
        data = {
            "server": {"base_url": "https://postino.example.com"},
            "database": {"url": "postgres://user:pass@db.example.com/postino"},
        }
        result = redact_config(data)
        assert result["database"]["url"] == "***REDACTED***"
        assert result["server"]["base_url"] == "https://postino.example.com"

    def test_source_database_url_redacted(self) -> None:
        data = {
            "sources": [
                {
                    "name": "CRM",
                    "database_url": "postgres://user:pass@crm.example.com/audience",
                }
            ]
        }
        result = redact_config(data)
        assert result["sources"][0]["name"] == "CRM"
        assert result["sources"][0]["database_url"] == "***REDACTED***"

    def test_dsn_redacted(self) -> None:
        data = {"sentry": {"dsn": "https://key@sentry.io/123"}}
        result = redact_config(data)
        assert result["sentry"]["dsn"] == "***REDACTED***"

    def test_unsubscribe_secret_redacted(self) -> None:
        data = {"security": {"unsubscribe_secret": "a" * 32}}
        result = redact_config(data)
        assert result["security"]["unsubscribe_secret"] == "***REDACTED***"

    def test_nested_password_redacted(self) -> None:
        data = {"email": {"smtp": {"password": "smtp-pass", "host": "smtp.example.com"}}}
        result = redact_config(data)
        assert result["email"]["smtp"]["password"] == "***REDACTED***"
        assert result["email"]["smtp"]["host"] == "smtp.example.com"

    def test_full_model_redaction(self) -> None:
        cfg = PostinoConfig.model_validate_json(
            """
            {
                "server": {
                    "secret_key": "test-secret-key-that-is-long-enough-yes",
                    "debug": true,
                    "allowed_hosts": ["localhost"],
                    "timezone": "UTC",
                    "base_url": "http://localhost:8000"
                },
                "database": {"url": "sqlite:///test.sqlite3"},
                "email": {
                    "provider": "resend",
                    "from_name": "Test",
                    "from_email": "test@example.com",
                    "resend": {"api_key": "re_secret_123"}
                },
                "security": {"unsubscribe_secret": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"},
                "gdpr": {},
                "branding": {},
                "sentry": {}
            }
            """
        )
        redacted = redact_config(cfg.model_dump(mode="json"))
        assert redacted["server"]["secret_key"] == "***REDACTED***"
        assert redacted["database"]["url"] == "***REDACTED***"
        assert redacted["email"]["resend"]["api_key"] == "***REDACTED***"
        assert redacted["security"]["unsubscribe_secret"] == "***REDACTED***"
        assert redacted["server"]["debug"] is True
        assert redacted["email"]["provider"] == "resend"
