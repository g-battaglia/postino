# CLAUDE.md — Postino

## Project Summary

Postino is a GDPR-first, self-hosted email marketing application built as a Django monolith. It handles subscriber management, automated email sequences, one-shot campaigns, and engagement analytics. All configuration via a single `config.toml`.

## Tech Stack

- Django 6.x + Python 3.12+
- PostgreSQL 16+ (via psycopg 3)
- HTMX 2 (UI interactivity, no SPA)
- Tailwind CSS 4 (styling, via CDN)
- Click (CLI)
- Resend / SMTP / SES / Mailgun (pluggable email)
- Pydantic (TOML config validation)
- Cron / systemd timers (scheduled background tasks)
- Django i18n (English default, Italian optional)

## Quick Start

```bash
cp config.example.toml config.toml
pip install -e ".[dev]"
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

## Project Layout

```
postino/
├── cli/              # Django project package (settings, urls, config loader, cli)
├── apps/
│   ├── subscribers/  # Subscriber CRUD, import, tags, health score, sync
│   ├── consent/      # GDPR consent, unsubscribe, preference center, data export/deletion
│   ├── campaigns/    # Campaigns, sequences, email sends
│   ├── templates_mgr/  # Email template management + renderer
│   ├── analytics/    # Dashboard metrics, churn analysis
│   ├── webhooks/     # Inbound webhooks from email providers
│   └── core/         # Shared: email backend abstraction, health check, base models
├── templates/        # Django templates (dashboard UI + email HTML)
├── static/           # JS (HTMX vendored)
├── design/           # Claude Design prototype (visual reference)
├── config.example.toml
└── pyproject.toml
```

## Two Interfaces

1. **Web Dashboard** — Django templates + HTMX, admin-only, full UI for all operations
2. **CLI** (`postino <command>`) — every core operation available for agents and scripts. All commands support `--json` for structured output.

## Key Architecture Decisions

- **TOML config** — single `config.toml` controls everything. Env vars override. Validated on startup.
- **No REST API** — CLI is the programmatic interface. Dashboard is server-side.
- **No task queue** — background tasks are Django management commands run via cron. No Celery, no Redis.
- **Append-only consent** — `ConsentRecord` and `UnsubscribeEvent` tables are never updated or deleted.
- **HMAC unsubscribe tokens** — deterministic, no DB lookup, no expiration.
- **Pluggable email** — provider selected via `config.toml`, abstracted behind `EmailBackend` interface.
- **Tailwind via CDN** — no build step for CSS. Design system follows the Claude Design prototype in `design/`.

## Commands

```bash
# Development
pip install -e ".[dev]"
ruff check .
pytest

# CLI
postino version
postino config validate
postino subscribers list --json
postino subscribers health --below 30
postino campaigns list
postino analytics overview --days 30 --json
postino gdpr audit user@example.com
postino sync run --dry-run

# Server
python manage.py runserver

# Background tasks (run via cron in production)
python manage.py check_scheduled_campaigns
python manage.py evaluate_sequences
python manage.py compute_health_scores
python manage.py cleanup_retention
python manage.py process_webhook_backlog
python manage.py sync_source
```

## Code Conventions

- Python 3.12+, type hints on public interfaces
- Ruff for linting/formatting (line-length 100)
- Django apps in `apps/` directory
- Models use `TimestampMixin` (created_at, updated_at)
- CLI uses Click, each app has its own `cli.py` module
- JSON output uses envelope: `{"ok": true, "data": {...}, "meta": {...}}`
- All subscriber-facing pages work without JavaScript
- All user-facing strings wrapped in `gettext` / `{% trans %}` for i18n
- Default language: English (`en`). Optional: Italian (`it`)

## Critical Rules

### GDPR & Privacy
- Consent records are APPEND-ONLY — never update or delete
- Unsubscribe events are NEVER deleted, even on GDPR erasure
- Suppressed subscribers are NEVER re-subscribed by sync or import
- Unsubscribe processing is SYNCHRONOUS — same HTTP request
- No tracking pixels by default (configurable)
- No link rewriting by default (configurable)
- Double opt-in is the default for new subscribers

### Unsubscribe (Most Critical Subsystem)
- Every email MUST have List-Unsubscribe + List-Unsubscribe-Post headers (RFC 8058)
- Every email MUST have visible unsubscribe link in footer
- Unsubscribe page MUST work without JavaScript
- No dark patterns: no "are you sure?", no guilt trips, no hidden re-subscribe
- HMAC tokens: no login required, no brute force possible
- Three unsubscribe levels: per-type, global, full data deletion

## Do NOT

- Add a REST API (CLI is the programmatic interface)
- Store secrets in config.example.toml or version control
- Make consent records mutable (no UPDATE/DELETE on ConsentRecord or UnsubscribeEvent)
- Re-subscribe suppressed users in any code path
- Add JavaScript frameworks (React, Vue, Alpine, etc.) — HTMX only for interactivity
- Add tracking pixels or link rewriting without explicit config opt-in
- Skip unsubscribe headers on any outgoing email
- Add Celery, Redis, or any task queue — use management commands + cron
