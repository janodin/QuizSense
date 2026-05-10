"""
Embedding service for QuizSense using DeepInfra API.
Model: sentence-transformers/all-MiniLM-L6-v2 (384 dimensions) via API.

RAM optimization:
- Offloads embedding generation to DeepInfra API, removing ~500MB local model footprint.
- Caches embeddings in Redis/in-memory to avoid redundant API calls.
- Batches requests for efficiency.
"""

import hashlib
import logging
import threading

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

# ─── Embedding cache ──────────────────────────────────────────────────────────
_embedding_cache: dict[str, list] = {}
_embedding_cache_lock = threading.Lock()
MAX_CACHE_SIZE = 2000

# ─── Tunables ───────────────────────────────────────────────────────────────
BATCH_SIZE = 16
DEEPINFRA_EMBEDDING_URL = "https://api.deepinfra.com/v1/openai/embeddings"
EMBEDDING_MODEL = "sentence-transformers/all-MiniLM-L6-v2"


def _get_cache_key(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def _get_cached_embedding(text: str) -> list | None:
    key = _get_cache_key(text)
    # Try Django Redis cache first (shared across workers)
    try:
        from django.core.cache import cache
        val = cache.get(f"emb:{key}")
        if val is not None:
            return val
    except Exception:
        pass
    # Fallback to in-memory cache
    with _embedding_cache_lock:
        return _embedding_cache.get(key)


def _set_cached_embedding(text: str, embedding: list) -> None:
    key = _get_cache_key(text)
    # Store in Django Redis cache (shared, persistent)
    try:
        from django.core.cache import cache
        cache.set(f"emb:{key}", embedding, timeout=60 * 60 * 24 * 7)
    except Exception:
        pass
    # Also store in in-memory fallback
    with _embedding_cache_lock:
        if len(_embedding_cache) >= MAX_CACHE_SIZE:
            items = list(_embedding_cache.items())
            _embedding_cache.clear()
            _embedding_cache.update(items[MAX_CACHE_SIZE // 2 :])
        _embedding_cache[key] = embedding


def _call_api_batch(texts: list[str]) -> list[list[float]]:
    """Call DeepInfra embedding API for a batch of texts."""
    api_key = getattr(settings, 'AI_PROVIDER_API_KEY', '')
    if not api_key:
        raise ValueError("AI_PROVIDER_API_KEY not set for embeddings")

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": EMBEDDING_MODEL,
        "input": texts,
    }

    response = requests.post(DEEPINFRA_EMBEDDING_URL, headers=headers, json=payload, timeout=60)
    response.raise_for_status()
    data = response.json()

    # Sort by index to ensure order matches input
    embeddings = sorted(data["data"], key=lambda x: x["index"])
    return [item["embedding"] for item in embeddings]


def embed_texts_batched(texts, batch_size=None):
    """
    Generate embeddings in batches via DeepInfra API.
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

    # Process missing texts in batches via API
    for i in range(0, len(missing_texts), batch_size):
        batch = missing_texts[i : i + batch_size]
        try:
            vectors = _call_api_batch(batch)
            for idx_in_batch, embedding in enumerate(vectors):
                original_idx = missing_indices[i + idx_in_batch]
                results[original_idx] = embedding
                _set_cached_embedding(missing_texts[i + idx_in_batch], embedding)
        except Exception as e:
            logger.error("DeepInfra embedding API call failed: %s", e)
            raise ValueError(f"Embedding generation failed: {e}")

    return results
