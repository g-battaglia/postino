# Requirements — Postino

## 1. Overview

Postino is a self-hosted, open-source email marketing application built as a Django monolith. It enables small SaaS operators to manage subscribers, send campaigns, run automated sequences, and track engagement — with GDPR compliance as a foundational constraint, not an afterthought.

### Target Users

Solo developers and small teams running SaaS products who need:
- Email retention (onboarding, re-engagement, win-back)
- Newsletter / digest campaigns
- Subscriber analytics with churn detection
- Full control over their data and compliance

### Non-Targets

- Enterprise marketing automation (use Mautic, HubSpot)
- Transactional email (use your app's own email service)
- High-volume bulk sending (>100k subscribers)

---

## 2. Functional Requirements

### FR-1: Subscriber Management

| ID | Requirement | Priority |
|----|-------------|----------|
| FR-1.1 | Store subscribers with email, name, status, source, metadata (JSONB), tags | Must |
| FR-1.2 | Subscriber statuses: `pending` (awaiting double opt-in), `active`, `unsubscribed`, `bounced`, `complained`, `deleted` | Must |
| FR-1.3 | Tag system: manual tags + auto-tagging rules (e.g., "tag as 'heavy-user' if metadata.requests > 500") | Should |
| FR-1.4 | Health score (0-100) computed from: days since last activity, email engagement, subscription tenure | Should |
| FR-1.5 | Import subscribers from CSV with field mapping UI | Must |
| FR-1.6 | Subscriber list view: searchable, filterable by status/tag/health/date, sortable, paginated | Must |
| FR-1.7 | Subscriber detail view: profile, consent history, email timeline, activity log | Must |
| FR-1.8 | Bulk actions: tag, untag, suppress, export CSV | Should |
| FR-1.9 | Manual subscriber creation via form | Must |
| FR-1.10 | Deduplicate by email on import/sync (case-insensitive) | Must |

### FR-2: Consent & GDPR

| ID | Requirement | Priority |
|----|-------------|----------|
| FR-2.1 | Every subscriber has per-email-type consent records with: timestamp, method, IP, proof text | Must |
| FR-2.2 | Double opt-in flow: subscriber receives confirmation email, must click to activate | Must |
| FR-2.3 | Double opt-in is configurable (can be disabled via TOML for imported/synced subscribers) | Must |
| FR-2.4 | Consent withdrawal is immediate and irreversible without new explicit consent | Must |
| FR-2.5 | Admin cannot re-subscribe a user who has unsubscribed (system-enforced) | Must |
| FR-2.6 | Consent audit log: append-only, records all consent grants and withdrawals | Must |
| FR-2.7 | Data export: subscriber can request all data held about them (JSON download via secure link) | Must |
| FR-2.8 | Data deletion: subscriber can request full erasure (Art. 17 GDPR) via secure link | Must |
| FR-2.9 | Configurable data retention: auto-purge unsubscribed subscriber data after N days | Should |
| FR-2.10 | Configurable email log retention: auto-purge send logs after N days | Should |

### FR-3: Unsubscribe (Critical Path)

| ID | Requirement | Priority |
|----|-------------|----------|
| FR-3.1 | RFC 8058 one-click unsubscribe: `List-Unsubscribe` + `List-Unsubscribe-Post` headers on every email | Must |
| FR-3.2 | Visible unsubscribe link in every email footer | Must |
| FR-3.3 | Unsubscribe landing page (no login required) with three options: (a) unsubscribe from this email type, (b) unsubscribe from all, (c) delete all my data | Must |
| FR-3.4 | Unsubscribe tokens: HMAC-SHA256, deterministic, no expiration, no DB lookup needed to verify | Must |
| FR-3.5 | Unsubscribe processing is synchronous — completed within the same HTTP request | Must |
| FR-3.6 | Preference center: page where subscriber can toggle which email types they receive | Should |
| FR-3.7 | Auto-suppress on hard bounce (provider webhook) | Must |
| FR-3.8 | Auto-suppress on spam complaint (provider webhook) | Must |
| FR-3.9 | Unsubscribe audit log: every event recorded with timestamp, method, IP, user-agent, related email | Must |
| FR-3.10 | Sync jobs MUST check suppression list before re-activating any subscriber | Must |
| FR-3.11 | Unsubscribe page works without JavaScript | Must |
| FR-3.12 | Confirmation page after unsubscribe shows clear message, no dark patterns, no "are you sure?" tricks | Must |

### FR-4: Email Types

| ID | Requirement | Priority |
|----|-------------|----------|
| FR-4.1 | Configurable email types (e.g., "weekly_digest", "onboarding", "product_update") | Must |
| FR-4.2 | Each type has: slug, display name, description, transactional flag | Must |
| FR-4.3 | Transactional emails bypass marketing consent (but still respect global unsubscribe) | Must |
| FR-4.4 | Subscribers consent per email type (granular) | Must |

### FR-5: Templates

| ID | Requirement | Priority |
|----|-------------|----------|
| FR-5.1 | Django/Jinja2 templates with subscriber context variables | Must |
| FR-5.2 | Base layout template (header, footer, unsubscribe link) | Must |
| FR-5.3 | Template editor in dashboard with live preview (HTMX) | Should |
| FR-5.4 | Auto-generate plain text version from HTML | Should |
| FR-5.5 | Template variables: subscriber.email, subscriber.name, subscriber.metadata.*, unsubscribe_url, preferences_url, current_date | Must |
| FR-5.6 | Template versioning: editing creates a new version, old sends reference their version | Could |

### FR-6: Campaigns (One-Shot Sends)

| ID | Requirement | Priority |
|----|-------------|----------|
| FR-6.1 | Create campaign: select template, write subject, select audience (filter by tag/status/health/date) | Must |
| FR-6.2 | Campaign states: `draft` → `scheduled` → `sending` → `sent` / `cancelled` | Must |
| FR-6.3 | Schedule campaign for future date/time | Must |
| FR-6.4 | Send test email to admin before sending to audience | Must |
| FR-6.5 | Campaign detail view: recipient count, delivery stats, open/click rates | Must |
| FR-6.6 | A/B subject line testing: split audience, pick winner after configurable delay | Could |
| FR-6.7 | Audience preview: show count + sample of matching subscribers before sending | Should |

### FR-7: Sequences (Automated Flows)

| ID | Requirement | Priority |
|----|-------------|----------|
| FR-7.1 | Multi-step email sequences with configurable delays between steps | Must |
| FR-7.2 | Triggers: subscriber created, tag added, manual enrollment, date-based | Must |
| FR-7.3 | Per-step conditions: send only if subscriber matches filter (e.g., health_score < 50, has tag X) | Should |
| FR-7.4 | Enrollment tracking: which subscribers are in which sequences, current step, status | Must |
| FR-7.5 | Sequence states: active, paused, archived | Must |
| FR-7.6 | Sequence editor: list of steps with template, delay, conditions | Must |
| FR-7.7 | Prevent duplicate enrollment (same subscriber in same sequence) | Must |
| FR-7.8 | Auto-cancel enrollment on unsubscribe | Must |

### FR-8: Analytics

| ID | Requirement | Priority |
|----|-------------|----------|
| FR-8.1 | Dashboard overview: total subscribers, active, MRR (if provided), health distribution | Must |
| FR-8.2 | Email metrics: sent, delivered, opened, clicked, bounced, complained — per campaign and aggregate | Must |
| FR-8.3 | Subscriber growth chart: new vs churned over time | Should |
| FR-8.4 | Sequence performance: completion rate, per-step drop-off | Should |
| FR-8.5 | Churn dashboard: at-risk subscribers (low health), cohort survival, zombie list | Should |
| FR-8.6 | All analytics computed server-side, rendered as HTML (SVG charts, data tables, CSS bars) | Must |
| FR-8.7 | No third-party analytics scripts (no Google Analytics, no external trackers) | Must |

### FR-9: Data Sources (Sync)

| ID | Requirement | Priority |
|----|-------------|----------|
| FR-9.1 | Configurable external data sources for subscriber sync | Should |
| FR-9.2 | Database source: connect to external PostgreSQL, define query + field mapping | Should |
| FR-9.3 | Sync runs as Django management command on cron schedule at configurable interval | Should |
| FR-9.4 | Sync respects suppression list: never re-activate unsubscribed/bounced/complained | Must |
| FR-9.5 | Sync log: records per-run stats (new, updated, errors) | Should |
| FR-9.6 | CSV import as one-time sync | Must |

### FR-10: Configuration

| ID | Requirement | Priority |
|----|-------------|----------|
| FR-10.1 | All configuration via single `config.toml` file | Must |
| FR-10.2 | TOML sections: server, database, email, security, gdpr, branding | Must |
| FR-10.3 | Environment variables override TOML values | Must |
| FR-10.4 | Email provider is pluggable via config: resend, smtp, ses, mailgun, console | Must |
| FR-10.5 | Validate config on startup: fail fast with clear error messages for missing/invalid values | Must |
| FR-10.6 | `config.example.toml` ships with the project, fully commented | Must |

### FR-11: CLI (Agentic Interface)

| ID | Requirement | Priority |
|----|-------------|----------|
| FR-11.1 | Full CLI (`postino` command) exposing all core operations: subscribers, campaigns, sequences, templates, analytics, sync, GDPR | Must |
| FR-11.2 | Every command supports `--json` flag for machine-readable structured output (JSON envelope with `ok`, `data`, `meta`) | Must |
| FR-11.3 | Human-readable output by default using Rich tables/panels | Must |
| FR-11.4 | Non-interactive: no prompts or confirmations (destructive actions require `--confirm` flag) | Must |
| FR-11.5 | Consistent exit codes: 0 = success, 1 = error, 2 = partial success | Must |
| FR-11.6 | `postino config validate` to check config.toml for errors without starting the server | Must |
| FR-11.7 | `postino gdpr export/delete/audit` commands for GDPR operations via CLI | Must |
| FR-11.8 | `postino sync run` to trigger data source sync on demand | Must |
| FR-11.9 | `postino analytics overview/churn` for agent-driven reporting | Must |
| FR-11.10 | CLI uses Django ORM directly (same models, same config.toml) — no separate API layer | Must |

### FR-12: Authentication

| ID | Requirement | Priority |
|----|-------------|----------|
| FR-12.1 | Django session auth for dashboard access | Must |
| FR-12.2 | Single admin user (created via `createsuperuser`) | Must |
| FR-12.3 | No public registration — dashboard is admin-only | Must |

---

## 3. Non-Functional Requirements

### NFR-1: Privacy & Compliance

| ID | Requirement |
|----|-------------|
| NFR-1.1 | GDPR (EU): consent records, data portability, right to erasure, breach logging |
| NFR-1.2 | CAN-SPAM (US): physical address in emails, clear sender identification, honor opt-out within 10 days |
| NFR-1.3 | CASL (Canada): express consent, sender identification, unsubscribe mechanism |
| NFR-1.4 | No tracking pixels by default (configurable opt-in for open tracking) |
| NFR-1.5 | No link rewriting by default (configurable opt-in for click tracking) |
| NFR-1.6 | All subscriber-facing pages work without JavaScript |
| NFR-1.7 | Unsubscribe token URLs must not leak subscriber email in the URL |
| NFR-1.8 | Physical address configurable in TOML, required before first send |

### NFR-2: Performance

| ID | Requirement |
|----|-------------|
| NFR-2.1 | Dashboard pages render in <500ms for up to 10,000 subscribers |
| NFR-2.2 | Campaign sending: 100 emails/minute minimum (provider-limited) |
| NFR-2.3 | Sequence evaluation: process all active enrollments in <60s |

### NFR-3: Security

| ID | Requirement |
|----|-------------|
| NFR-3.1 | HMAC-SHA256 for unsubscribe tokens (no sequential IDs, no brute force) |
| NFR-3.2 | Webhook signature verification (Resend/Svix, Mailgun, SES) |
| NFR-3.3 | CSRF protection on all forms |
| NFR-3.4 | Content Security Policy headers |
| NFR-3.5 | No secrets in config.example.toml or version control |
| NFR-3.6 | Config validation prevents running with default/empty secrets |

### NFR-4: Deployment

| ID | Requirement |
|----|-------------|
| NFR-4.1 | Single-command local setup: `pip install -e . && python manage.py migrate && python manage.py runserver` |
| NFR-4.2 | Docker Compose for production: app + PostgreSQL + cron |
| NFR-4.3 | Railway / Render / Fly.io compatible |
| NFR-4.4 | Health check endpoint at `/health/` |

### NFR-5: Code Quality

| ID | Requirement |
|----|-------------|
| NFR-5.1 | Python 3.12+, type hints on public interfaces |
| NFR-5.2 | Ruff for linting and formatting |
| NFR-5.3 | pytest for testing |
| NFR-5.4 | 80%+ test coverage on consent/unsubscribe paths |

### NFR-6: Internationalization

| ID | Requirement |
|----|-------------|
| NFR-6.1 | Default language: English (`en`) |
| NFR-6.2 | Optional language: Italian (`it`) |
| NFR-6.3 | All user-facing dashboard strings use Django i18n (`{% trans %}`, `gettext_lazy`) |
| NFR-6.4 | Public pages (unsubscribe, preferences) support i18n |
| NFR-6.5 | Language selected automatically via browser `Accept-Language` header |
| NFR-6.6 | CLI output is English-only (no i18n on CLI) |

---

## 4. Out of Scope (v1)

- Multi-tenant / multi-user
- REST / GraphQL API
- Drag-and-drop email builder
- SMS / push notifications
- Landing page builder
- Signup form builder / embeddable widgets
- Advanced segmentation (behavioral triggers, event streams)
- Multi-language email content (email body localization is out of scope; dashboard i18n is in scope)
