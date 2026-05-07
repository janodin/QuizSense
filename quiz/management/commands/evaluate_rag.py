import logging
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone
from django.db.models import Avg
from quiz.models import RetrievalLog

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Evaluate RAG retrieval metrics from RetrievalLog records."

    def add_arguments(self, parser):
        parser.add_argument(
            "--since-days",
            type=int,
            default=None,
            help="Only consider logs from the last N days.",
        )

    def handle(self, *args, **options):
        since_days = options["since_days"]
        qs = RetrievalLog.objects.all()

        if since_days is not None:
            cutoff = timezone.now() - timedelta(days=since_days)
            qs = qs.filter(created_at__gte=cutoff)
            self.stdout.write(
                self.style.NOTICE(f"Filtering to logs from the last {since_days} days (since {cutoff:%Y-%m-%d %H:%M}).")
            )

        total = qs.count()
        if total == 0:
            self.stdout.write(self.style.WARNING("No RetrievalLog records found."))
            return

        # ─── Aggregate metrics ───────────────────────────────────────────────
        avg_latency = qs.aggregate(avg=Avg('retrieval_latency_ms'))['avg'] or 0.0
        avg_sim = qs.aggregate(avg=Avg('avg_similarity_top_k'))['avg'] or 0.0
        min_sim = qs.aggregate(avg=Avg('min_similarity_top_k'))['avg'] or 0.0
        max_sim = qs.aggregate(avg=Avg('max_similarity_top_k'))['avg'] or 0.0

        session_present = qs.filter(session_chunk_count__gt=0).count()
        textbook_present = qs.filter(textbook_chunk_count__gt=0).count()
        both_present = qs.filter(session_chunk_count__gt=0, textbook_chunk_count__gt=0).count()

        pct_session = (session_present / total) * 100
        pct_textbook = (textbook_present / total) * 100
        pct_both = (both_present / total) * 100

        # ─── Top-1 similarity distribution ───────────────────────────────────
        # We look at the avg_similarity_top_k as a proxy for overall quality.
        buckets = {
            '0.0-0.2': 0,
            '0.2-0.4': 0,
            '0.4-0.6': 0,
            '0.6-0.8': 0,
            '0.8-1.0': 0,
        }
        for val in qs.exclude(avg_similarity_top_k=None).values_list('avg_similarity_top_k', flat=True):
            if val < 0.2:
                buckets['0.0-0.2'] += 1
            elif val < 0.4:
                buckets['0.2-0.4'] += 1
            elif val < 0.6:
                buckets['0.4-0.6'] += 1
            elif val < 0.8:
                buckets['0.6-0.8'] += 1
            else:
                buckets['0.8-1.0'] += 1

        # ─── Report ──────────────────────────────────────────────────────────
        self.stdout.write(self.style.SUCCESS("=" * 60))
        self.stdout.write(self.style.SUCCESS("RAG EVALUATION REPORT"))
        self.stdout.write(self.style.SUCCESS("=" * 60))
        self.stdout.write(f"Total retrieval logs:       {total}")
        self.stdout.write(f"Avg retrieval latency:      {avg_latency:.2f} ms")
        self.stdout.write(f"Avg top-k similarity:       {avg_sim:.4f}")
        self.stdout.write(f"Avg min similarity (top-k): {min_sim:.4f}")
        self.stdout.write(f"Avg max similarity (top-k): {max_sim:.4f}")
        self.stdout.write("")
        self.stdout.write("Source Presence:")
        self.stdout.write(f"  >0 session chunks:        {session_present} ({pct_session:.1f}%)")
        self.stdout.write(f"  >0 textbook chunks:       {textbook_present} ({pct_textbook:.1f}%)")
        self.stdout.write(f"  Both sources present:     {both_present} ({pct_both:.1f}%)")
        self.stdout.write("")
        self.stdout.write("Similarity Distribution (avg_similarity_top_k):")
        for label, count in buckets.items():
            bar = "█" * (count * 40 // total) if total else ""
            self.stdout.write(f"  {label}: {count:>6}  {bar}")
        self.stdout.write(self.style.SUCCESS("=" * 60))
