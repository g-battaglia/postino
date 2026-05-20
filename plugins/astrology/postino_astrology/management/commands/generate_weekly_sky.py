"""Generate and schedule a Weekly Sky Briefing campaign.

Calls the Astrologer API for current sky data, creates a campaign with the
``weekly-sky-briefing`` template, and schedules it for next Monday 07:00 UTC.

Usage::

    python manage.py generate_weekly_sky
    python manage.py generate_weekly_sky --send-now
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from postino_astrology.client import fetch_sky_data


def _next_monday_7am() -> datetime:
    now = datetime.now(UTC)
    days_until_monday = (7 - now.weekday()) % 7
    if days_until_monday == 0 and now.hour >= 7:
        days_until_monday = 7
    monday = now.replace(hour=7, minute=0, second=0, microsecond=0)
    return monday + timedelta(days=days_until_monday)


class Command(BaseCommand):
    help = "Generate and schedule a Weekly Sky Briefing campaign from Astrologer API data."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--send-now",
            action="store_true",
            help="Schedule for immediate send instead of next Monday 07:00 UTC.",
        )

    def handle(self, *args, **options) -> None:
        from apps.campaigns.models import Campaign
        from apps.consent.models import EmailType
        from apps.templates_mgr.models import EmailTemplate

        plugin_config = getattr(settings, "POSTINO_PLUGINS_CONFIG", {}).get("astrology", {})
        if not plugin_config.get("enabled"):
            raise CommandError("Astrology plugin is not enabled in config.toml.")

        api_url = plugin_config.get("api_url", "")
        api_key = plugin_config.get("api_key", "")
        api_key_header = plugin_config.get("api_key_header", "X-AstrologerStudio-Proxy-Secret")

        if not api_url or not api_key:
            raise CommandError("api_url and api_key must be set in [plugins.astrology].")

        self.stdout.write("Fetching sky data from Astrologer API...")
        sky = fetch_sky_data(
            api_url=api_url,
            api_key=api_key,
            api_key_header=api_key_header,
            latitude=plugin_config.get("default_latitude", 0.0),
            longitude=plugin_config.get("default_longitude", 0.0),
        )
        if sky is None:
            raise CommandError("Failed to fetch sky data — check API config and connectivity.")

        try:
            template = EmailTemplate.objects.get(slug="weekly-sky-briefing")
        except EmailTemplate.DoesNotExist:
            raise CommandError(
                "Template 'weekly-sky-briefing' not found. Create it first."
            ) from None

        try:
            email_type = EmailType.objects.get(slug="weekly-sky")
        except EmailType.DoesNotExist:
            raise CommandError(
                "EmailType 'weekly-sky' not found. Create it in Settings > Email Types."
            ) from None

        scheduled_at = (
            datetime.now(UTC) if options["send_now"] else _next_monday_7am()
        )

        campaign = Campaign.objects.create(
            name=f"Weekly Sky {sky['week_label']}",
            email_type=email_type,
            template=template,
            subject_line=f"The Sky This Week — {sky['week_label']}",
            status="scheduled",
            scheduled_at=scheduled_at,
            audience_filter={"status": "active"},
        )

        self.stdout.write(self.style.SUCCESS(
            f"Campaign '{campaign.name}' (ID={campaign.pk}) "
            f"scheduled for {scheduled_at.strftime('%Y-%m-%d %H:%M UTC')}."
        ))
        self.stdout.write(
            f"  Moon: {sky['moon_phase']['emoji']} {sky['moon_phase']['phase_name']}"
        )
        self.stdout.write(f"  Planets: {len(sky['planets'])} positions loaded")
        retros = sky.get("retrograde_planets", [])
        if retros:
            names = ", ".join(p["name"] for p in retros)
            self.stdout.write(f"  Retrogrades: {names}")
