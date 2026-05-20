"""Management command to recompute health scores for all active subscribers.

Acquires a PostgreSQL advisory lock to prevent concurrent execution.
Designed to run daily at 03:00 via cron:
    0 3 * * * cd /app && python manage.py compute_health_scores
"""

from __future__ import annotations

from django.core.management.base import BaseCommand

from apps.core.locks import advisory_lock
from apps.subscribers.services import compute_all_health_scores


class Command(BaseCommand):
    help = "Recompute health scores for all active subscribers."

    def handle(self, *args, **options) -> None:
        with advisory_lock("compute_health_scores") as acquired:
            if not acquired:
                self.stdout.write("Another instance is running. Exiting.")
                return

            self.stdout.write("Computing health scores...")
            result = compute_all_health_scores()

            self.stdout.write(
                f"Processed {result.total} subscribers: "
                f"{result.updated} updated. "
                f"Distribution: {result.distribution['healthy']} healthy, "
                f"{result.distribution['at_risk']} at-risk, "
                f"{result.distribution['critical']} critical."
            )
