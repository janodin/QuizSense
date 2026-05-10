"""
Management command placeholder.
Embeddings are now handled via the DeepInfra API, so no local model pre-warming is required.

Usage:
    python manage.py prewarm_embeddings
"""
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Placeholder: Embeddings are now API-based and do not require local pre-warming."

    def handle(self, *args, **options):
        self.stdout.write(
            self.style.SUCCESS(
                "Embeddings are now generated via the DeepInfra API. No local model to pre-warm."
            )
        )
