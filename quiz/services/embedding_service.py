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
import hashlib
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

# ─── Embedding cache (Fix #4) ───────────────────────────────────────────────
# Simple in-memory cache keyed by MD5(content) → embedding vector.
# Avoids re-embedding identical chunks across files or re-uploads.
_embedding_cache: dict[str, list] = {}
_embedding_cache_lock = threading.Lock()
MAX_CACHE_SIZE = 2000             # max cached embeddings before eviction


# ─── Tunables (can be overridden via settings or environment) ───────────────
IDLE_TIMEOUT_SECONDS = 120        # unload model after 2 min of no encoding activity
BATCH_SIZE = 16                   # chunks per encode() call — faster on modern CPUs
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
                # malloc_trim is Linux-only; skip on Windows.
                import sys
                if sys.platform.startswith("linux"):
                    try:
                        import ctypes.util, ctypes
                        libc_name = ctypes.util.find_library("c")
                        if libc_name:
                            libc = ctypes.CDLL(libc_name)
                            libc.malloc_trim(0)
                    except Exception:
                        pass


# ─── Cache helpers ───────────────────────────────────────────────────────────

def _get_cache_key(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def _get_cached_embedding(text: str) -> list | None:
    key = _get_cache_key(text)
    with _embedding_cache_lock:
        return _embedding_cache.get(key)


def _set_cached_embedding(text: str, embedding: list) -> None:
    key = _get_cache_key(text)
    with _embedding_cache_lock:
        # Simple LRU-style eviction: if over limit, clear half the cache
        if len(_embedding_cache) >= MAX_CACHE_SIZE:
            items = list(_embedding_cache.items())
            _embedding_cache.clear()
            _embedding_cache.update(items[MAX_CACHE_SIZE // 2 :])
        _embedding_cache[key] = embedding


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

    # Check cache first
    results = [None] * len(texts)
    missing_indices = []
    missing_texts = []

    for i, text in enumerate(texts):
        cached = _get_cached_embedding(text)
        if cached is not None:
            results[i] = cached
        else:
            missing_indices.append(i)
            missing_texts.append(text)

    if not missing_texts:
        return results

    model = _get_model()
    _touch()

    for i in range(0, len(missing_texts), BATCH_SIZE):
        batch = missing_texts[i : i + BATCH_SIZE]
        vectors = model.encode(batch, normalize_embeddings=True)
        for idx_in_batch, v in enumerate(vectors):
            embedding = v.tolist()
            original_idx = missing_indices[i + idx_in_batch]
            results[original_idx] = embedding
            _set_cached_embedding(missing_texts[i + idx_in_batch], embedding)
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

    # Check cache first
    results = [None] * len(texts)
    missing_indices = []
    missing_texts = []

    for i, text in enumerate(texts):
        cached = _get_cached_embedding(text)
        if cached is not None:
            results[i] = cached
        else:
            missing_indices.append(i)
            missing_texts.append(text)

    if not missing_texts:
        return results

    model = _get_model()
    _touch()

    for i in range(0, len(missing_texts), batch_size):
        batch = missing_texts[i : i + batch_size]
        vectors = model.encode(batch, normalize_embeddings=True)
        for idx_in_batch, v in enumerate(vectors):
            embedding = v.tolist()
            original_idx = missing_indices[i + idx_in_batch]
            results[original_idx] = embedding
            _set_cached_embedding(missing_texts[i + idx_in_batch], embedding)
        del vectors

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

    # Check cache first
    results = [None] * len(texts)
    missing_indices = []
    missing_texts = []

    for i, text in enumerate(texts):
        cached = _get_cached_embedding(text)
        if cached is not None:
            results[i] = cached
        else:
            missing_indices.append(i)
            missing_texts.append(text)

    if not missing_texts:
        return results

    model = _get_model()
    _touch()

    for i in range(0, len(missing_texts), batch_size):
        batch = missing_texts[i : i + batch_size]
        vectors = model.encode(batch, normalize_embeddings=True)
        for idx_in_batch, v in enumerate(vectors):
            embedding = v.tolist()
            original_idx = missing_indices[i + idx_in_batch]
            results[original_idx] = embedding
            _set_cached_embedding(missing_texts[i + idx_in_batch], embedding)
        del vectors

    _maybe_unload()
    return results
