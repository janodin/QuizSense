import logging
from django.core.management.base import BaseCommand
from django.utils import timezone
from quiz.models import GenerationMetric

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Delete generation metrics older than a specified number of days (default: 30)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--days",
            type=int,
            default=30,
            help="Number of days to keep metrics for (default: 30).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show how many records would be deleted without actually deleting.",
        )

    def handle(self, *args, **options):
        days = options["days"]
        dry_run = options["dry_run"]
        cutoff = timezone.now() - timezone.timedelta(days=days)

        queryset = GenerationMetric.objects.filter(created_at__lt=cutoff)
        count = queryset.count()

        if dry_run:
            self.stdout.write(
                self.style.WARNING(
                    f"[DRY RUN] Would delete {count} metrics older than {days} days (before {cutoff:%Y-%m-%d %H:%M})."
                )
            )
        else:
            deleted, _ = queryset.delete()
            self.stdout.write(
                self.style.SUCCESS(
                    f"Deleted {deleted} metrics older than {days} days (before {cutoff:%Y-%m-%d %H:%M})."
                )
            )
