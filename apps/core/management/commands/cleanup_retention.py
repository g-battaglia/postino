"""Management command to purge expired data per configured retention policies.

Applies two retention rules from config.toml:
- ``gdpr.unsubscribed_retention_days``: purges personal data of
  unsubscribed/deleted subscribers past the retention window.
- ``gdpr.email_log_retention_days``: deletes EmailSend records older than
  the configured window.

CRITICAL INVARIANTS:
- ConsentRecord and UnsubscribeEvent rows are NEVER deleted or mutated.
- This command only removes EmailSend logs and blanks subscriber personal
  fields for long-suppressed subscribers.

Designed to run daily at 04:00 via cron:
    0 4 * * * cd /app && python manage.py cleanup_retention
"""

from __future__ import annotations

from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db.models import Q
from django.utils import timezone

from apps.campaigns.models import EmailSend
from apps.core.locks import advisory_lock
from apps.subscribers.models import Subscriber


class Command(BaseCommand):
    help = "Purge expired data per configured retention policies."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be deleted without actually deleting.",
        )

    def handle(self, *args, **options) -> None:
        dry_run = options["dry_run"]

        with advisory_lock("cleanup_retention") as acquired:
            if not acquired:
                self.stdout.write("Another instance is running. Exiting.")
                return

            now = timezone.now()

            email_logs_deleted = self._cleanup_email_logs(now, dry_run)
            subscribers_purged = self._cleanup_subscriber_data(now, dry_run)

            if dry_run:
                self.stdout.write("[DRY RUN] No changes were made.")

            self.stdout.write(
                f"Email logs {'would be ' if dry_run else ''}deleted: {email_logs_deleted}. "
                f"Subscriber data {'would be ' if dry_run else ''}purged: {subscribers_purged}."
            )

    def _cleanup_email_logs(self, now, dry_run: bool) -> int:
        """Delete EmailSend records past the configured retention window."""
        retention_days = getattr(settings, "POSTINO_EMAIL_LOG_RETENTION_DAYS", 730)
        if retention_days <= 0:
            self.stdout.write("Email log retention is disabled (0 days). Skipping.")
            return 0

        cutoff = now - timedelta(days=retention_days)
        qs = EmailSend.objects.filter(sent_at__lt=cutoff)

        if dry_run:
            count = qs.count()
        else:
            count, _ = qs.delete()
            if isinstance(count, dict):
                count = sum(count.values())

        return count

    def _cleanup_subscriber_data(self, now, dry_run: bool) -> int:
        """Blank personal fields of long-suppressed subscribers past retention.

        Does NOT delete ConsentRecord or UnsubscribeEvent rows.
        Only blanks: name, metadata, ip_address, source_id.
        The subscriber row is preserved for suppression lookup.
        """
        retention_days = getattr(settings, "POSTINO_UNSUBSCRIBED_RETENTION_DAYS", 90)
        if retention_days <= 0:
            self.stdout.write("Subscriber retention is disabled (0 days). Skipping.")
            return 0

        cutoff = now - timedelta(days=retention_days)
        suppressed_statuses = ["unsubscribed", "bounced", "complained", "deleted"]

        qs = Subscriber.objects.filter(
            status__in=suppressed_statuses,
            updated_at__lt=cutoff,
        ).filter(
            Q(name__gt="")
            | ~Q(metadata={})
            | Q(ip_address__isnull=False)
            | Q(source_id__gt="")
            | Q(double_optin_token__isnull=False)
            | Q(double_optin_confirmed_at__isnull=False)
        )

        if dry_run:
            return qs.count()

        purged = 0
        for subscriber in qs.iterator(chunk_size=500):
            subscriber.name = ""
            subscriber.metadata = {}
            subscriber.ip_address = None
            subscriber.source_id = ""
            subscriber.double_optin_token = None
            subscriber.double_optin_confirmed_at = None
            subscriber.save(update_fields=[
                "name", "metadata", "ip_address", "source_id",
                "double_optin_token", "double_optin_confirmed_at", "updated_at",
            ])
            purged += 1

        return purged
