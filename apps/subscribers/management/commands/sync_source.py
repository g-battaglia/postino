"""Management command to sync subscribers from external data sources.

Acquires a PostgreSQL advisory lock to prevent concurrent execution.
Designed to run every 6 hours via cron:
    0 */6 * * * cd /app && python manage.py sync_source
"""

from __future__ import annotations

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.core.locks import advisory_lock
from apps.subscribers.models import DataSource, SyncLog


class Command(BaseCommand):
    help = "Sync subscribers from configured external data sources."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--source",
            type=str,
            default=None,
            help="Sync only the named data source (default: all active sources).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            default=False,
            help="Preview changes without writing to the database.",
        )

    def handle(self, *args, **options) -> None:
        source_name: str | None = options["source"]
        dry_run: bool = options["dry_run"]

        with advisory_lock("sync_source") as acquired:
            if not acquired:
                self.stdout.write("Another instance is running. Exiting.")
                return

            sources = DataSource.objects.filter(is_active=True)
            if source_name:
                sources = sources.filter(name=source_name)

            if not sources.exists():
                self.stdout.write("No active data sources found.")
                return

            for source in sources:
                self._sync_one(source, dry_run)

    def _sync_one(self, source: DataSource, dry_run: bool) -> None:
        from apps.subscribers.services import sync_data_source

        now = timezone.now()
        log = SyncLog.objects.create(
            data_source=source,
            status=SyncLog.Status.DRY_RUN if dry_run else SyncLog.Status.RUNNING,
            started_at=now,
        )

        self.stdout.write(f"Syncing '{source.name}'{' (dry run)' if dry_run else ''}...")

        try:
            result = sync_data_source(source, dry_run=dry_run)
        except Exception as exc:
            log.status = SyncLog.Status.ERROR
            log.error_details = {"error": str(exc)}
            log.completed_at = timezone.now()
            log.save()
            self.stderr.write(f"  Error: {exc}")
            return

        log.new_count = result.new_count
        log.updated_count = result.updated_count
        log.skipped_count = result.skipped_count
        log.suppressed_count = result.suppressed_count
        log.status = SyncLog.Status.DRY_RUN if dry_run else SyncLog.Status.SUCCESS
        if result.errors:
            log.error_details = {"errors": result.errors}
            if not dry_run:
                log.status = SyncLog.Status.ERROR
        log.completed_at = timezone.now()
        log.save()

        if not dry_run:
            source.last_sync_at = log.completed_at
            source.save(update_fields=["last_sync_at", "updated_at"])

        self.stdout.write(
            f"  Done: {result.new_count} new, {result.updated_count} updated, "
            f"{result.skipped_count} skipped, {result.suppressed_count} suppressed."
        )
        if result.errors:
            for err in result.errors:
                self.stderr.write(f"  Error: {err}")
