"""
Benchmark script for comparing PyTorch vs ONNX embedding performance.

Run with:
    python manage.py benchmark_embeddings
    python manage.py benchmark_embeddings --samples 100 --batch-size 32

Outputs timing results for both providers.
"""

import json
import time
from django.core.management.base import BaseCommand
from django.conf import settings


class Command(BaseCommand):
    help = "Benchmark PyTorch vs ONNX embedding performance"

    def add_arguments(self, parser):
        parser.add_argument(
            '--samples',
            type=int,
            default=50,
            help='Number of sample texts to embed (default: 50)',
        )
        parser.add_argument(
            '--batch-size',
            type=int,
            default=16,
            help='Batch size for embedding (default: 16)',
        )
        parser.add_argument(
            '--warmup',
            type=int,
            default=3,
            help='Number of warmup runs before timing (default: 3)',
        )

    def handle(self, *args, **options):
        num_samples = options['samples']
        batch_size = options['batch_size']
        warmup_runs = options['warmup']

        # Generate sample texts of varying lengths
        sample_texts = self._generate_samples(num_samples)

        self.stdout.write(f"Benchmarking with {num_samples} samples, batch_size={batch_size}\n")

        # ─── PyTorch Benchmark ────────────────────────────────────────────────
        self.stdout.write("=" * 60)
        self.stdout.write("PyTorch (sentence-transformers) Benchmark")
        self.stdout.write("=" * 60)

        pytorch_results = self._benchmark_pytorch(sample_texts, batch_size, warmup_runs)

        # ─── ONNX Benchmark ───────────────────────────────────────────────────
        self.stdout.write("\n" + "=" * 60)
        self.stdout.write("ONNX Runtime Benchmark")
        self.stdout.write("=" * 60)

        onnx_results = self._benchmark_onnx(sample_texts, batch_size, warmup_runs)

        # ─── Summary ──────────────────────────────────────────────────────────
        self.stdout.write("\n" + "=" * 60)
        self.stdout.write("BENCHMARK SUMMARY")
        self.stdout.write("=" * 60)

        if pytorch_results['success'] and onnx_results['success']:
            pytorch_time = pytorch_results['total_time']
            onnx_time = onnx_results['total_time']
            speedup = pytorch_time / onnx_time if onnx_time > 0 else float('inf')

            self.stdout.write(f"\nPyTorch total time:  {pytorch_time:.3f}s")
            self.stdout.write(f"ONNX total time:     {onnx_time:.3f}s")
            self.stdout.write(f"Speedup:             {speedup:.2f}x")
            self.stdout.write(f"\nPyTorch per-sample:  {pytorch_results['per_sample_ms']:.2f}ms")
            self.stdout.write(f"ONNX per-sample:     {onnx_results['per_sample_ms']:.2f}ms")

            if speedup > 1.0:
                self.stdout.write(self.style.SUCCESS(
                    f"\nONNX is {speedup:.2f}x faster than PyTorch for this workload!"
                ))
            else:
                self.stdout.write(self.style.WARNING(
                    f"\nPyTorch is {1/speedup:.2f}x faster than ONNX for this workload."
                ))
        elif pytorch_results['success']:
            self.stdout.write(self.style.WARNING("\nONNX benchmark failed — PyTorch is the fallback."))
            self.stdout.write(f"PyTorch total time: {pytorch_results['total_time']:.3f}s")
        else:
            self.stdout.write(self.style.ERROR("\nBoth benchmarks failed!"))

        # Save results to file
        results = {
            'pytorch': pytorch_results,
            'onnx': onnx_results,
            'config': {
                'samples': num_samples,
                'batch_size': batch_size,
                'warmup_runs': warmup_runs,
            }
        }

        results_path = settings.BASE_DIR / 'benchmark_results.json'
        with open(results_path, 'w') as f:
            json.dump(results, f, indent=2, default=str)
        self.stdout.write(f"\nResults saved to {results_path}")

    def _generate_samples(self, num_samples):
        """Generate sample texts of varying lengths."""
        base_texts = [
            "Python is a high-level, general-purpose programming language. "
            "Its design philosophy emphasizes code readability with the use of significant indentation. "
            "Python is dynamically typed and garbage-collected. It supports multiple programming paradigms, "
            "including structured, object-oriented and functional programming.",

            "A variable is a named location in memory that stores a value. "
            "In Python, variables are created when you assign a value to them. "
            "Python has no command for declaring a variable. "
            "Variables can store different data types: strings, integers, floats, booleans, lists, etc.",

            "Functions are reusable blocks of code that perform a specific task. "
            "In Python, you define a function using the def keyword followed by the function name "
            "and parentheses. Functions can take parameters and return values. "
            "They help organize code and reduce duplication.",

            "Loops allow you to execute a block of code multiple times. "
            "Python has two main loop types: for loops and while loops. "
            "A for loop iterates over a sequence (list, tuple, string, etc.). "
            "A while loop continues executing as long as a condition is true.",

            "Object-oriented programming (OOP) is a programming paradigm based on objects. "
            "Objects contain data (attributes) and code (methods). "
            "Classes are blueprints for creating objects. "
            "OOP principles include encapsulation, inheritance, and polymorphism.",

            "Exception handling allows you to gracefully handle errors in your code. "
            "In Python, you use try/except blocks to catch and handle exceptions. "
            "You can also use finally to execute cleanup code regardless of whether an error occurred. "
            "Custom exceptions can be created by subclassing the Exception class.",

            "Lists are ordered, mutable collections in Python. "
            "They can store items of different types and support operations like append, remove, sort, and slice. "
            "List comprehensions provide a concise way to create lists based on existing iterables.",

            "Dictionaries store key-value pairs in Python. "
            "They are unordered (before Python 3.7), mutable, and indexed by keys. "
            "Dictionary keys must be immutable types like strings, numbers, or tuples. "
            "Dictionaries are optimized for fast lookups.",

            "String manipulation is a common task in programming. "
            "Python provides many built-in string methods like split(), join(), strip(), "
            "upper(), lower(), replace(), and format(). "
            "F-strings (formatted string literals) provide a concise way to embed expressions in strings.",

            "File I/O operations allow you to read from and write to files. "
            "In Python, you use the open() function with modes like 'r' (read), 'w' (write), "
            "'a' (append). It's best practice to use the 'with' statement to ensure files are properly closed.",
        ]

        samples = []
        for i in range(num_samples):
            # Vary length by concatenating base texts
            num_parts = (i % 5) + 1
            text = " ".join(base_texts[j % len(base_texts)] for j in range(num_parts))
            samples.append(text)

        return samples

    def _benchmark_pytorch(self, texts, batch_size, warmup_runs):
        """Benchmark PyTorch/sentence-transformers embedding."""
        from quiz.services.embedding_service import _get_pytorch_model, embed_texts_batched

        # Warmup
        self.stdout.write(f"\nPyTorch warmup ({warmup_runs} runs)...")
        for _ in range(warmup_runs):
            _get_pytorch_model()
            embed_texts_batched(texts[:5], batch_size=batch_size)

        # Timed run
        self.stdout.write(f"PyTorch timed run ({len(texts)} samples, batch_size={batch_size})...")
        start = time.time()
        results = embed_texts_batched(texts, batch_size=batch_size)
        total_time = time.time() - start

        success = len(results) == len(texts) and all(r is not None for r in results)

        self.stdout.write(f"  Total time: {total_time:.3f}s")
        if success:
            per_sample_ms = (total_time / len(texts)) * 1000
            self.stdout.write(f"  Per sample: {per_sample_ms:.2f}ms")
            self.stdout.write(f"  Samples/sec: {len(texts) / total_time:.1f}")
        else:
            self.stdout.write(self.style.ERROR("  FAILED: Not all embeddings were generated"))

        return {
            'success': success,
            'total_time': total_time,
            'per_sample_ms': (total_time / len(texts)) * 1000 if success else None,
            'samples_per_sec': len(texts) / total_time if success else None,
            'num_samples': len(texts),
            'batch_size': batch_size,
        }

    def _benchmark_onnx(self, texts, batch_size, warmup_runs):
        """Benchmark ONNX Runtime embedding."""
        from quiz.services.embedding_service import ONNXEmbeddingProvider

        provider = ONNXEmbeddingProvider()

        if not provider._ensure_available():
            self.stdout.write(self.style.WARNING("  ONNX Runtime not available — skipping benchmark"))
            return {
                'success': False,
                'error': 'ONNX Runtime not available',
                'total_time': None,
                'per_sample_ms': None,
                'samples_per_sec': None,
                'num_samples': len(texts),
                'batch_size': batch_size,
            }

        # Warmup
        self.stdout.write(f"\nONNX warmup ({warmup_runs} runs)...")
        for _ in range(warmup_runs):
            provider.encode(texts[:5])

        # Timed run
        self.stdout.write(f"ONNX timed run ({len(texts)} samples, batch_size={batch_size})...")
        start = time.time()
        results = provider.encode_batched(texts, batch_size=batch_size)
        total_time = time.time() - start

        success = len(results) == len(texts) and all(r is not None for r in results)

        self.stdout.write(f"  Total time: {total_time:.3f}s")
        if success:
            per_sample_ms = (total_time / len(texts)) * 1000
            self.stdout.write(f"  Per sample: {per_sample_ms:.2f}ms")
            self.stdout.write(f"  Samples/sec: {len(texts) / total_time:.1f}")
        else:
            self.stdout.write(self.style.ERROR("  FAILED: Not all embeddings were generated"))

        return {
            'success': success,
            'total_time': total_time,
            'per_sample_ms': (total_time / len(texts)) * 1000 if success else None,
            'samples_per_sec': len(texts) / total_time if success else None,
            'num_samples': len(texts),
            'batch_size': batch_size,
        }
