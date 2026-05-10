"""
Benchmark DeepInfra embedding API throughput.

Usage:
    python manage.py benchmark_embeddings
    python manage.py benchmark_embeddings --samples 100 --batch-size 32
"""

import json
import time

from django.conf import settings
from django.core.management.base import BaseCommand

from quiz.services.embedding_service import EMBEDDING_MODEL, embed_texts_batched


class Command(BaseCommand):
    help = "Benchmark configured embedding API throughput"

    def add_arguments(self, parser):
        parser.add_argument(
            "--samples",
            type=int,
            default=50,
            help="Number of sample texts to embed (default: 50)",
        )
        parser.add_argument(
            "--batch-size",
            type=int,
            default=16,
            help="Batch size for embedding (default: 16)",
        )

    def handle(self, *args, **options):
        num_samples = options["samples"]
        batch_size = options["batch_size"]
        sample_texts = self._generate_samples(num_samples)

        self.stdout.write(
            f"Benchmarking {EMBEDDING_MODEL} via API with "
            f"{num_samples} samples, batch_size={batch_size}\n"
        )

        start = time.time()
        results = embed_texts_batched(sample_texts, batch_size=batch_size)
        total_time = time.time() - start

        success = len(results) == len(sample_texts) and all(r is not None for r in results)
        per_sample_ms = (total_time / len(sample_texts)) * 1000 if success else None
        samples_per_sec = len(sample_texts) / total_time if success and total_time else None

        if success:
            self.stdout.write(self.style.SUCCESS("Embedding benchmark completed."))
            self.stdout.write(f"Total time:     {total_time:.3f}s")
            self.stdout.write(f"Per sample:     {per_sample_ms:.2f}ms")
            self.stdout.write(f"Samples/sec:    {samples_per_sec:.1f}")
            self.stdout.write(f"Dimensions:     {len(results[0])}")
        else:
            self.stdout.write(self.style.ERROR("Embedding benchmark failed."))

        output = {
            "provider": "api",
            "model": EMBEDDING_MODEL,
            "success": success,
            "total_time": total_time,
            "per_sample_ms": per_sample_ms,
            "samples_per_sec": samples_per_sec,
            "num_samples": len(sample_texts),
            "batch_size": batch_size,
            "dimensions": len(results[0]) if success and results else None,
        }

        results_path = settings.BASE_DIR / "benchmark_results.json"
        with open(results_path, "w") as f:
            json.dump(output, f, indent=2, default=str)
        self.stdout.write(f"\nResults saved to {results_path}")

    def _generate_samples(self, num_samples):
        base_texts = [
            "Python is a high-level programming language focused on readability.",
            "Variables store values and can reference strings, numbers, lists, and objects.",
            "Functions organize reusable logic and may accept parameters or return values.",
            "Loops repeat blocks of code while iterating over data or checking conditions.",
            "Object-oriented programming organizes behavior around classes and objects.",
        ]
        return [base_texts[i % len(base_texts)] for i in range(num_samples)]
