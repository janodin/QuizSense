"""
Embedding service for QuizSense using sentence-transformers.
Model: paraphrase-MiniLM-L3-v2 (384 dimensions) — tiny, fast, low RAM, runs locally.
"""

import logging
from functools import lru_cache

from django.conf import settings

logger = logging.getLogger(__name__)

# Lazy-load the model to avoid importing heavy deps at startup
_transformer_model = None


def _get_model():
    """Load and cache the sentence-transformer model."""
    global _transformer_model
    if _transformer_model is None:
        from sentence_transformers import SentenceTransformer
        # all-MiniLM-L6-v2: 384 dimensions, much better retrieval quality than MiniLM-L3
        # Still CPU-friendly (~90MB), semantic similarity benchmarks significantly higher
        _transformer_model = SentenceTransformer("all-MiniLM-L6-v2")
        logger.info("sentence-transformers model loaded: all-MiniLM-L6-v2")
    return _transformer_model


def embed_texts(texts):
    """
    Generate embeddings using sentence-transformers/paraphrase-MiniLM-L3-v2.
    Returns list of 384-dim float lists.
    """
    if not texts:
        return []

    model = _get_model()
    # Explicitly set batch_size=64 for faster processing on multicore CPUs
    vectors = model.encode(texts, batch_size=64, normalize_embeddings=True)
    # Convert numpy arrays to plain Python lists for pgvector
    return [v.tolist() for v in vectors]


def embed_texts_batched(texts, batch_size=32):
    """
    Generate embeddings in batches — useful for large textbook ingestion.
    Returns list of 384-dim float lists.
    """
    if not texts:
        return []

    model = _get_model()
    all_vectors = []
    for i in range(0, len(texts), batch_size):
        batch = texts[i:i + batch_size]
        vectors = model.encode(batch, normalize_embeddings=True)
        all_vectors.extend([v.tolist() for v in vectors])
    return all_vectors
