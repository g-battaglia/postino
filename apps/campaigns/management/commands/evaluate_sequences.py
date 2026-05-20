"""Management command to evaluate all active sequence enrollments.

Acquires a PostgreSQL advisory lock to prevent concurrent execution.
Runs the sequence evaluation engine which processes active enrollments,
sends due emails, and auto-cancels enrollments for suppressed subscribers.

Designed to run every 30 minutes via cron:
    */30 * * * * cd /app && python manage.py evaluate_sequences
"""

from __future__ import annotations

from django.core.management.base import BaseCommand

from apps.campaigns.services import evaluate_sequences
from apps.core.locks import advisory_lock


class Command(BaseCommand):
    help = "Evaluate active sequence enrollments and send due emails."

    def handle(self, *args, **options) -> None:
        with advisory_lock("evaluate_sequences") as acquired:
            if not acquired:
                self.stdout.write("Another instance is running. Exiting.")
                return

            self.stdout.write("Evaluating sequences...")
            result = evaluate_sequences()

            self.stdout.write(
                f"Processed {result.enrollments_processed} enrollments: "
                f"{result.emails_sent} sent, "
                f"{result.emails_skipped} skipped, "
                f"{result.enrollments_completed} completed, "
                f"{result.enrollments_cancelled} cancelled."
            )

            if result.errors:
                self.stderr.write(f"Errors ({len(result.errors)}):")
                for error in result.errors:
                    self.stderr.write(f"  - {error}")
