"""Management command: find and send all scheduled campaigns that are due.

Acquires a PostgreSQL advisory lock so that multiple cron invocations do
not send the same campaigns twice. Designed to run every minute from cron.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.campaigns.models import Campaign
from apps.campaigns.services import CampaignSendError, send_campaign
from apps.core.locks import advisory_lock


class Command(BaseCommand):
    help = "Send all scheduled campaigns whose scheduled_at has passed."

    def handle(self, *args, **options) -> None:
        with advisory_lock("check_scheduled_campaigns") as acquired:
            if not acquired:
                self.stderr.write(
                    self.style.ERROR("Another instance is already running.")
                )
                return

            now = timezone.now()
            campaigns = Campaign.objects.filter(
                status=Campaign.Status.SCHEDULED,
                scheduled_at__lte=now,
            ).order_by("scheduled_at")

            campaign_ids = list(campaigns.values_list("pk", flat=True))

            if not campaign_ids:
                self.stdout.write("No scheduled campaigns are due.")
                return

            self.stdout.write(f"Found {len(campaign_ids)} campaign(s) to send.")

            total_sent = 0
            total_failed = 0

            for campaign_id in campaign_ids:
                try:
                    result = send_campaign(campaign_id)
                    self.stdout.write(
                        f"  Campaign '{result.campaign_name}' "
                        f"(id={campaign_id}): "
                        f"sent={result.sent}, skipped={result.skipped}, "
                        f"failed={result.failed}"
                    )
                    total_sent += result.sent
                    total_failed += result.failed
                except CampaignSendError as exc:
                    self.stderr.write(
                        self.style.ERROR(f"  Campaign {campaign_id}: {exc}")
                    )
                    total_failed += 1

        self.stdout.write(
            f"Done. Total sent={total_sent}, total failures={total_failed}."
        )
