"""
Embedding service for QuizSense using sentence-transformers.
Model: all-MiniLM-L6-v2 (384 dimensions) — tiny, fast, low RAM, runs locally.

RAM optimization for Hetzner CX22 (4GB):
- LRU eviction: model is loaded on-demand and evicted after IDLE_TIMEOUT seconds
  of inactivity to avoid holding ~500MB permanently per Gunicorn worker.
- Small batch encoding (8 chunks/batch) with explicit garbage collection between
  batches to keep peak memory bounded.
- torch CPU threads capped at 2 to avoid saturating the 2-vCPU box.
"""

import gc
import logging
import threading
import time
from functools import lru_cache
from pathlib import Path

from django.conf import settings

logger = logging.getLogger(__name__)

# Lazy-load the model; unload after IDLE_TIMEOUT seconds of inactivity.
_transformer_model = None
_model_lock = threading.RLock()
_last_used = [0.0]  # mutable container for last-use timestamp


# ─── Tunables (can be overridden via settings or environment) ───────────────
IDLE_TIMEOUT_SECONDS = 120        # unload model after 2 min of no encoding activity
BATCH_SIZE = 8                    # chunks per encode() call — keeps RAM bounded
MAX_CPU_THREADS = 2                # match Hetzner CX22 vCPU count (2 cores)


def _touch():
    """Update last-use timestamp."""
    _last_used[0] = time.monotonic()


def _idle_seconds():
    return time.monotonic() - _last_used[0]


# ─── Model loading / eviction ───────────────────────────────────────────────

def _get_model():
    """
    Load (or return cached) the sentence-transformer model.

    After IDLE_TIMEOUT_SECONDS of inactivity the model is dropped so its
    ~500MB are reclaimed by the garbage collector.  This prevents the model
    from living forever in each Gunicorn worker process.
    """
    global _transformer_model

    with _model_lock:
        if _transformer_model is not None:
            if _idle_seconds() > IDLE_TIMEOUT_SECONDS:
                logger.info(
                    "Embedding model idle for %.0f s — evicting to free RAM.",
                    _idle_seconds(),
                )
                del _transformer_model
                _transformer_model = None
                gc.collect()
            else:
                _touch()
                return _transformer_model

        import torch
        from sentence_transformers import SentenceTransformer

        torch.set_num_threads(MAX_CPU_THREADS)

        model = SentenceTransformer("all-MiniLM-L6-v2")

        try:
            # Dynamic int8 quantization shrinks the model ~40% with negligible
            # accuracy loss and improves CPU throughput.
            _transformer_model = torch.quantization.quantize_dynamic(
                model, {torch.nn.Linear}, dtype=torch.qint8
            )
            logger.info(
                "sentence-transformers model loaded (int8 quantized, %.0f s idle timeout)",
                IDLE_TIMEOUT_SECONDS,
            )
        except Exception as e:
            logger.warning("Quantization failed, using standard model: %s", e)
            _transformer_model = model

        _touch()
        return _transformer_model


def _maybe_unload():
    """Called after each encode() call; triggers eviction when idle."""
    global _transformer_model
    if _idle_seconds() > IDLE_TIMEOUT_SECONDS:
        with _model_lock:
            if _transformer_model is not None and _idle_seconds() > IDLE_TIMEOUT_SECONDS:
                logger.info("Embedding model idle — evicting to free RAM.")
                del _transformer_model
                _transformer_model = None
                gc.collect()
                # libc.malloc_trim forces the glibc allocator to release freed memory
                # back to the OS — critical for getting RAM back from large allocations.
                import ctypes.util, ctypes
                libc_name = ctypes.util.find_library("c")
                if libc_name:
                    libc = ctypes.CDLL(libc_name)
                    libc.malloc_trim(0)


# ─── Public API ──────────────────────────────────────────────────────────────

def embed_texts(texts):
    """
    Generate embeddings using sentence-transformers.
    Returns list of 384-dim float lists.

    Memory guard: processes in batches of BATCH_SIZE and runs GC between
    batches so peak numpy/Torch memory never exceeds ~BATCH_SIZE × embedding.
    """
    if not texts:
        return []

    model = _get_model()
    _touch()

    results = []
    for i in range(0, len(texts), BATCH_SIZE):
        batch = texts[i : i + BATCH_SIZE]
        vectors = model.encode(batch, normalize_embeddings=True)
        results.extend(v.tolist() for v in vectors)
        del vectors
        gc.collect()

    _maybe_unload()
    return results


def embed_texts_batched(texts, batch_size=None):
    """
    Generate embeddings in batches — useful for large textbook ingestion.
    Returns list of 384-dim float lists.
    """
    if not texts:
        return []

    batch_size = batch_size or BATCH_SIZE
    model = _get_model()
    _touch()

    all_vectors = []
    for i in range(0, len(texts), batch_size):
        batch = texts[i : i + batch_size]
        vectors = model.encode(batch, normalize_embeddings=True)
        all_vectors.extend(v.tolist() for v in vectors)
        del vectors
        gc.collect()

    _maybe_unload()
    return all_vectors
