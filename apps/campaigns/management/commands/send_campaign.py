"""Management command: send a single campaign by ID.

Acquires a PostgreSQL advisory lock to prevent concurrent sends of the
same campaign. Emits a human-readable summary on completion.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from apps.campaigns.services import CampaignSendError, send_campaign
from apps.core.locks import advisory_lock


class Command(BaseCommand):
    help = "Send a campaign to its eligible audience."

    def add_arguments(self, parser) -> None:
        parser.add_argument("campaign_id", type=int, help="Primary key of the campaign to send.")

    def handle(self, *args, **options) -> None:
        campaign_id: int = options["campaign_id"]
        lock_name = f"send_campaign_{campaign_id}"

        with advisory_lock(lock_name) as acquired:
            if not acquired:
                self.stderr.write(
                    self.style.ERROR(
                        f"Another instance is already sending campaign {campaign_id}."
                    )
                )
                raise CommandError("Advisory lock not acquired.")

            try:
                result = send_campaign(campaign_id)
            except CampaignSendError as exc:
                raise CommandError(str(exc)) from exc

        self.stdout.write(
            f"Campaign '{result.campaign_name}' (id={result.campaign_id}) sent."
        )
        self.stdout.write(f"  Eligible: {result.eligible}")
        self.stdout.write(f"  Sent:     {result.sent}")
        self.stdout.write(f"  Skipped:  {result.skipped}")
        self.stdout.write(f"  Failed:   {result.failed}")

        if result.errors:
            self.stdout.write(self.style.WARNING("  Errors:"))
            for error in result.errors:
                self.stdout.write(f"    - {error}")

        if result.failed > 0:
            raise CommandError(f"Campaign sent with {result.failed} failure(s).")
