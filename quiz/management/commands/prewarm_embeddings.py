"""
Management command to pre-warm the sentence-transformers embedding model.
Run this after deployment to ensure the first user request doesn't pay
the 30-60s cold-start penalty.

Usage:
    python manage.py prewarm_embeddings
"""
import time
from django.core.management.base import BaseCommand

from quiz.services.embedding_service import _get_model


class Command(BaseCommand):
    help = "Pre-warm the sentence-transformers embedding model"

    def handle(self, *args, **options):
        self.stdout.write("Loading sentence-transformers model (paraphrase-MiniLM-L3-v2)...")
        start = time.time()
        model = _get_model()
        elapsed = time.time() - start
        self.stdout.write(
            self.style.SUCCESS(
                f"Model loaded in {elapsed:.1f}s — {model.get_sentence_embedding_dimension()} dimensions"
            )
        )
