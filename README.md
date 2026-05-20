<p align="center">
  <img src="https://img.shields.io/badge/Django-6.x-092E20?logo=django&logoColor=white" alt="Django 6">
  <img src="https://img.shields.io/badge/Python-3.12+-3776AB?logo=python&logoColor=white" alt="Python 3.12+">
  <img src="https://img.shields.io/badge/HTMX-2.x-3366CC?logo=htmx&logoColor=white" alt="HTMX">
  <img src="https://img.shields.io/badge/Tailwind_CSS-4-06B6D4?logo=tailwindcss&logoColor=white" alt="Tailwind CSS">
  <img src="https://img.shields.io/badge/License-MIT-green" alt="MIT License">
  <img src="https://img.shields.io/badge/Tests-1080_passing-brightgreen" alt="Tests">
</p>

<h1 align="center">📮 Postino</h1>

<p align="center">
  <strong>Open-source email marketing you actually own.</strong><br>
  GDPR-first. Self-hosted. Extensible with plugins.<br>
  Built with Django + HTMX — no JavaScript framework, no build step.
</p>

---

## ✨ Features

| | |
|---|---|
| 👥 **Subscribers** | Import, tag, health scoring, sync from external databases |
| 📧 **Campaigns** | One-shot sends with audience filtering, scheduling, template preview |
| 🔄 **Sequences** | Automated multi-step flows — onboarding, re-engagement, win-back |
| ✏️ **Template Editor** | Split-pane with live preview, Django template syntax, variable reference |
| 📊 **Analytics** | Delivery metrics, churn analysis, cohort retention, health distribution |
| 💻 **CLI** | Every operation via `postino <command> --json` — built for agents and scripts |
| 🔌 **Plugins** | Extend with custom content providers ([example: astrology](plugins/astrology/)) |
| 🌍 **i18n** | English + Italian, auto-detected from browser |
| ❓ **Contextual Help** | Floating "?" button on every page explains that section |

## 🔒 GDPR by Architecture

Not a checkbox — it's how the whole thing is built.

| | |
|---|---|
| 📜 **Append-only audit log** | Consent records are never updated, never deleted |
| 🚪 **3-level unsubscribe** | Per email type, global, or full data deletion |
| ⚡ **RFC 8058 one-click** | Unsubscribe header on every email — Gmail/Yahoo compliant |
| 🔐 **HMAC tokens** | No login required, no brute force possible |
| ✅ **Double opt-in** | Enabled by default for new subscribers |
| 🚫 **No tracking by default** | No pixels, no link rewriting — unless you explicitly opt in |
| 🛡️ **Suppression invariant** | Unsubscribed users can never be re-subscribed by any code path |

## 🚀 Quick Start

```bash
git clone https://github.com/g-battaglia/postino.git
cd postino
cp config.example.toml config.toml   # edit with your settings
pip install -e ".[dev]"
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

Background tasks run via cron — no Celery, no Redis:

```bash
python manage.py check_scheduled_campaigns   # every minute
python manage.py evaluate_sequences          # every 30 min
python manage.py compute_health_scores       # daily
```

## ⚙️ Configuration

One file controls everything: **`config.toml`**

```toml
[server]
secret_key = "..."
debug = true
base_url = "https://your-domain.com"

[database]
url = "postgresql://postino:postino@localhost:5432/postino"

[email]
provider = "smtp"                       # resend | smtp | ses | mailgun | console
from_name = "My Newsletter"
from_email = "hello@example.com"

[gdpr]
require_double_optin = true
enable_open_tracking = false

[plugins.astrology]                     # optional plugin
enabled = true
api_key = "your-rapidapi-key"
```

## 🔌 Plugins

Postino discovers plugins via Python entry points. Install a plugin, add its config to `config.toml`, done.

### 🔮 [postino-astrology](plugins/astrology/)

Adds real-time planetary positions and moon phases to email templates via the [Astrologer API](https://github.com/g-battaglia/Astrologer-API).

```bash
pip install -e plugins/astrology
```

```toml
[plugins.astrology]
enabled = true
api_key = "your-rapidapi-key"   # from kerykeion.net/astrologer-api/subscribe
```

Templates use `{% if sky %}` guards — if the plugin isn't installed, emails work normally.

**Write your own:** implement `configure()` and `enrich_context()`, register via `pyproject.toml` entry point. See [plugins/astrology/](plugins/astrology/) for the full pattern.

## 🛠️ Stack

| | |
|---|---|
| **Backend** | Django 6, Python 3.12+ |
| **UI** | HTMX + Tailwind CSS (CDN) |
| **Database** | PostgreSQL (or SQLite for dev) |
| **Email** | Resend · SMTP · SES · Mailgun |
| **Tasks** | Management commands + cron |
| **CLI** | Click, `--json` on everything |

No JavaScript framework. No task queue. No build step.

## 📖 Documentation

| | |
|---|---|
| 📘 **[Manual (EN)](manual/en/README.md)** | Full user guide in English |
| 📗 **[Manuale (IT)](manual/it/README.md)** | Guida completa in italiano |
| 📋 **[Requirements](REQUIREMENTS.md)** | Functional and non-functional specs |
| 🏗️ **[CLAUDE.md](CLAUDE.md)** | Development conventions and rules |

## 📄 License

MIT — do whatever you want with it.
