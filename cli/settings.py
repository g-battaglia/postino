"""Django settings for Postino.

All runtime values come from config.toml (or POSTINO_* env var overrides).
The TOML config is loaded and validated by postino.config on import.
If no valid config is found, import fails with a clear error.
"""

from importlib import metadata
from pathlib import Path

import dj_database_url

from cli.config import load_config

# Build paths inside the project like this: BASE_DIR / "subdir".
BASE_DIR = Path(__file__).resolve().parent.parent

# --- Load TOML configuration (fail-fast, no fallback) ----------------------

_config = load_config()

# --- Security ---------------------------------------------------------------

SECRET_KEY = _config.server.secret_key
DEBUG = _config.server.debug
ALLOWED_HOSTS = _config.server.allowed_hosts

# --- Application definition -------------------------------------------------

INSTALLED_APPS: list[str] = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django_htmx",
    "apps.core",
    "apps.subscribers",
    "apps.consent",
    "apps.campaigns",
    "apps.templates_mgr",
    "apps.analytics",
    "apps.webhooks",
]

MIDDLEWARE: list[str] = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "django_htmx.middleware.HtmxMiddleware",
]

ROOT_URLCONF = "cli.urls"

TEMPLATES: list[dict] = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "cli.wsgi.application"

# --- Database ---------------------------------------------------------------

DATABASES = {"default": dj_database_url.parse(_config.database.url)}

# --- Password validation ----------------------------------------------------

AUTH_PASSWORD_VALIDATORS: list[dict] = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# --- Internationalization ---------------------------------------------------

LANGUAGE_CODE = "en"

USE_I18N = True

USE_TZ = True

LANGUAGES = [
    ("en", "English"),
    ("it", "Italiano"),
]

LOCALE_PATHS = [BASE_DIR / "locale"]

TIME_ZONE = _config.server.timezone

# --- Static files -----------------------------------------------------------

STATIC_URL = "static/"

STATICFILES_DIRS: list[Path] = [BASE_DIR / "static"]

# --- Default primary key field type -----------------------------------------

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- Auth redirects --------------------------------------------------------

LOGIN_URL = "/admin/login/"
LOGIN_REDIRECT_URL = "/"

# --- Content Security Policy (baseline) ------------------------------------

SECURE_BROWSER_XSS_FILTER = True
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"

# --- Postino-specific settings -----------------------------------------------

POSTINO_UNSUBSCRIBE_SECRET: str = _config.security.unsubscribe_secret
POSTINO_BASE_URL: str = _config.server.base_url.rstrip("/")

# Email provider settings (non-secret)
POSTINO_EMAIL_PROVIDER: str = _config.email.provider
POSTINO_EMAIL_FROM_NAME: str = _config.email.from_name
POSTINO_EMAIL_FROM_EMAIL: str = _config.email.from_email
POSTINO_EMAIL_REPLY_TO: str = _config.email.reply_to

# Resend API key (secret, accessed only by the email backend)
POSTINO_RESEND_API_KEY: str = _config.email.resend.api_key
POSTINO_RESEND_WEBHOOK_SIGNING_SECRET: str = _config.email.resend.webhook_signing_secret

# SMTP settings (accessed by SMTPBackend)
POSTINO_SMTP_HOST: str = _config.email.smtp.host
POSTINO_SMTP_PORT: int = _config.email.smtp.port
POSTINO_SMTP_USERNAME: str = _config.email.smtp.username
POSTINO_SMTP_PASSWORD: str = _config.email.smtp.password
POSTINO_SMTP_USE_TLS: bool = _config.email.smtp.use_tls
POSTINO_SMTP_USE_SSL: bool = _config.email.smtp.use_ssl

# SES settings (accessed by SESBackend)
POSTINO_SES_AWS_ACCESS_KEY_ID: str = _config.email.ses.aws_access_key_id
POSTINO_SES_AWS_SECRET_ACCESS_KEY: str = _config.email.ses.aws_secret_access_key
POSTINO_SES_AWS_REGION: str = _config.email.ses.aws_region

# Mailgun settings (accessed by MailgunBackend)
POSTINO_MAILGUN_API_KEY: str = _config.email.mailgun.api_key
POSTINO_MAILGUN_DOMAIN: str = _config.email.mailgun.domain
POSTINO_MAILGUN_WEBHOOK_SIGNING_KEY: str = _config.email.mailgun.webhook_signing_key

# Branding (used in email templates and dashboard)
POSTINO_APP_NAME: str = _config.branding.app_name
POSTINO_PRIMARY_COLOR: str = _config.branding.primary_color
POSTINO_LOGO_URL: str = _config.branding.logo_url

# GDPR
POSTINO_REQUIRE_DOUBLE_OPTIN: bool = _config.gdpr.require_double_optin
POSTINO_UNSUBSCRIBED_RETENTION_DAYS: int = _config.gdpr.unsubscribed_retention_days
POSTINO_EMAIL_LOG_RETENTION_DAYS: int = _config.gdpr.email_log_retention_days
POSTINO_PHYSICAL_ADDRESS: str = _config.gdpr.physical_address
POSTINO_ENABLE_OPEN_TRACKING: bool = _config.gdpr.enable_open_tracking
POSTINO_ENABLE_CLICK_TRACKING: bool = _config.gdpr.enable_click_tracking

# --- Postino version --------------------------------------------------------

try:
    POSTINO_VERSION = metadata.version("postino")
except metadata.PackageNotFoundError:
    POSTINO_VERSION = "0.1.0"

# --- Plugins ----------------------------------------------------------------

POSTINO_PLUGINS_CONFIG: dict = _config.plugins

from apps.core.plugins import discover_and_configure as _discover_plugins  # noqa: E402

_discover_plugins(POSTINO_PLUGINS_CONFIG)
