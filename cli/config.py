"""TOML configuration loader with Pydantic validation and environment variable overrides."""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, model_validator


class ConfigError(Exception):
    """Raised when configuration is invalid, missing, or cannot be loaded."""


# ---------------------------------------------------------------------------
# Pydantic settings models (one per config.toml section)
# ---------------------------------------------------------------------------


class ServerConfig(BaseModel):
    secret_key: str = ""
    debug: bool = True
    allowed_hosts: list[str] = Field(default_factory=lambda: ["localhost", "127.0.0.1"])
    timezone: str = "UTC"
    base_url: str = ""


class DatabaseConfig(BaseModel):
    url: str = ""


class ResendProviderConfig(BaseModel):
    api_key: str = ""
    webhook_signing_secret: str = ""


class SMTPProviderConfig(BaseModel):
    host: str = ""
    port: int = 587
    username: str = ""
    password: str = ""
    use_tls: bool = True
    use_ssl: bool = False


class SESProviderConfig(BaseModel):
    aws_access_key_id: str = ""
    aws_secret_access_key: str = ""
    aws_region: str = "eu-west-1"


class MailgunProviderConfig(BaseModel):
    api_key: str = ""
    domain: str = ""
    webhook_signing_key: str = ""


class EmailConfig(BaseModel):
    provider: str = "console"
    from_name: str = "Postino"
    from_email: str = "hello@example.com"
    reply_to: str = ""
    resend: ResendProviderConfig = Field(default_factory=ResendProviderConfig)
    smtp: SMTPProviderConfig = Field(default_factory=SMTPProviderConfig)
    ses: SESProviderConfig = Field(default_factory=SESProviderConfig)
    mailgun: MailgunProviderConfig = Field(default_factory=MailgunProviderConfig)


class SecurityConfig(BaseModel):
    unsubscribe_secret: str = ""


class GDPRConfig(BaseModel):
    require_double_optin: bool = True
    unsubscribed_retention_days: int = 90
    email_log_retention_days: int = 730
    enable_open_tracking: bool = False
    enable_click_tracking: bool = False
    physical_address: str = ""


class BrandingConfig(BaseModel):
    app_name: str = "Postino"
    primary_color: str = "#6366f1"
    logo_url: str = ""


class SentryConfig(BaseModel):
    dsn: str = ""
    traces_sample_rate: float = 0.1


class SourceConfig(BaseModel):
    name: str
    type: str
    enabled: bool = True
    sync_interval_hours: int = 6
    database_url: str = ""
    query: str = ""
    field_map: dict[str, str] = Field(default_factory=dict)
    tag: str = ""
    metadata_fields: list[str] = Field(default_factory=list)


class PostinoConfig(BaseModel):
    server: ServerConfig = Field(default_factory=ServerConfig)
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    email: EmailConfig = Field(default_factory=EmailConfig)
    security: SecurityConfig = Field(default_factory=SecurityConfig)
    gdpr: GDPRConfig = Field(default_factory=GDPRConfig)
    branding: BrandingConfig = Field(default_factory=BrandingConfig)
    sentry: SentryConfig = Field(default_factory=SentryConfig)
    sources: list[SourceConfig] = Field(default_factory=list)
    plugins: dict[str, dict[str, Any]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_cross_field_rules(self) -> PostinoConfig:
        if not self.server.secret_key:
            raise ValueError("server.secret_key must not be empty")
        if len(self.security.unsubscribe_secret) < 32:
            raise ValueError(
                "security.unsubscribe_secret must be at least 32 characters"
            )
        valid_providers = {"resend", "smtp", "ses", "mailgun", "console"}
        if self.email.provider not in valid_providers:
            raise ValueError(
                f"email.provider must be one of {sorted(valid_providers)}, "
                f"got '{self.email.provider}'"
            )
        if not self.database.url:
            raise ValueError("database.url must not be empty")
        if not self.server.debug and self.email.provider == "console":
            raise ValueError(
                "email.provider cannot be 'console' when server.debug is false"
            )
        if not self.server.base_url:
            raise ValueError("server.base_url must not be empty")
        return self


# ---------------------------------------------------------------------------
# Secret field names used for redaction
# ---------------------------------------------------------------------------

_SECRET_FIELD_NAMES = frozenset({
    "secret_key",
    "api_key",
    "password",
    "webhook_signing_secret",
    "webhook_signing_key",
    "aws_secret_access_key",
    "aws_access_key_id",
    "unsubscribe_secret",
    "database_url",
    "dsn",
})

_SECRET_FIELD_PATHS = frozenset({
    ("database", "url"),
})

_REDACTED = "***REDACTED***"


def _redact_value(name: str, value: Any, path: tuple[str, ...] = ()) -> Any:
    is_secret = name in _SECRET_FIELD_NAMES or path in _SECRET_FIELD_PATHS
    if is_secret and isinstance(value, str) and value:
        return _REDACTED
    return value


def redact_config(data: dict[str, Any], path: tuple[str, ...] = ()) -> dict[str, Any]:
    """Return a deep copy with secret string fields replaced by a redaction marker."""
    result: dict[str, Any] = {}
    for key, value in data.items():
        child_path = (*path, key)
        if isinstance(value, dict):
            result[key] = redact_config(value, child_path)
        elif isinstance(value, list):
            result[key] = [
                redact_config(item, child_path)
                if isinstance(item, dict)
                else _redact_value(key, item, child_path)
                for item in value
            ]
        else:
            result[key] = _redact_value(key, value, child_path)
    return result


# ---------------------------------------------------------------------------
# Config path resolution
# ---------------------------------------------------------------------------

_DEFAULT_CONFIG_NAME = "config.toml"


def _resolve_config_path(path: str | Path | None = None) -> Path:
    if path is not None:
        p = Path(path)
        if p.is_file():
            return p
        raise ConfigError(f"Config file not found: {p}")

    env_path = os.environ.get("POSTINO_CONFIG")
    if env_path:
        p = Path(env_path)
        if p.is_file():
            return p
        raise ConfigError(f"Config file from POSTINO_CONFIG not found: {p}")

    local = Path(_DEFAULT_CONFIG_NAME)
    if local.is_file():
        return local

    raise ConfigError(
        "No config.toml found. Create one from config.example.toml, "
        "or set POSTINO_CONFIG to point to your config file."
    )


# ---------------------------------------------------------------------------
# Environment variable overrides
# ---------------------------------------------------------------------------

_ENV_PREFIX = "POSTINO_"
_ENV_SEPARATOR = "__"


def _parse_env_value(raw: str, field_info: Any | None = None) -> Any:
    if field_info is not None:
        annotation = field_info.annotation
        if annotation is bool:
            return raw.lower() in ("1", "true", "yes", "on")
        if annotation is int:
            return int(raw)
        if annotation is float:
            return float(raw)
    if raw.lower() in ("1", "true", "yes", "on"):
        return True
    if raw.lower() in ("0", "false", "no", "off"):
        return False
    try:
        return int(raw)
    except ValueError:
        pass
    try:
        return float(raw)
    except ValueError:
        pass
    if "," in raw:
        return [item.strip() for item in raw.split(",")]
    return raw


def _apply_env_overrides(data: dict[str, Any], model_cls: type[BaseModel]) -> dict[str, Any]:
    """Apply POSTINO_* env vars to the config dict, respecting nesting via double underscore."""
    data = dict(data)
    prefix = _ENV_PREFIX

    for env_key, env_value in os.environ.items():
        if not env_key.startswith(prefix):
            continue
        remainder = env_key[len(prefix) :].lower()
        parts = remainder.split(_ENV_SEPARATOR)

        _set_nested(data, parts, env_value, model_cls)

    return data


def _set_nested(
    data: dict[str, Any],
    parts: list[str],
    raw_value: str,
    model_cls: type[BaseModel],
) -> None:
    current = data
    field_map = model_cls.model_fields

    for i, part in enumerate(parts):
        is_last = i == len(parts) - 1

        if is_last:
            field_info = field_map.get(part)
            current[part] = _parse_env_value(raw_value, field_info)
            return

        if part not in current:
            current[part] = {}

        next_value = current[part]
        if isinstance(next_value, list):
            idx = i + 1
            if idx < len(parts) and parts[idx].isdigit():
                list_idx = int(parts[idx])
                while len(next_value) <= list_idx:
                    next_value.append({})
                current = next_value[list_idx]
                field_map = _get_sub_field_map(model_cls, parts[: idx + 1])
            else:
                current = next_value[-1] if next_value else {}
                field_map = _get_sub_field_map(model_cls, parts[: idx + 1])
        elif isinstance(next_value, dict):
            current = next_value
            field_map = _get_sub_field_map(model_cls, parts[: i + 1])


def _get_sub_field_map(
    model_cls: type[BaseModel], path: list[str]
) -> dict[str, Any]:
    current_model = model_cls
    for part in path:
        if part.isdigit():
            continue
        field = current_model.model_fields.get(part)
        if field is None:
            return {}
        ann = field.annotation
        if ann is None:
            return {}
        origin = getattr(ann, "__origin__", None)
        if origin is list:
            args = getattr(ann, "__args__", ())
            if args:
                inner = args[0]
                if isinstance(inner, type) and issubclass(inner, BaseModel):
                    current_model = inner
        elif isinstance(ann, type) and issubclass(ann, BaseModel):
            current_model = ann
        else:
            return {}
    return current_model.model_fields


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def load_config(path: str | Path | None = None) -> PostinoConfig:
    """Load, merge (env overrides), and validate the TOML configuration.

    Raises ConfigError if the file is missing or validation fails.
    """
    config_path = _resolve_config_path(path)
    try:
        with open(config_path, "rb") as fh:
            raw = tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"Invalid TOML in {config_path}: {exc}") from exc

    raw = _apply_env_overrides(raw, PostinoConfig)

    try:
        return PostinoConfig.model_validate(raw)
    except Exception as exc:
        raise ConfigError(str(exc)) from exc
