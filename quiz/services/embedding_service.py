"""
Embedding service for QuizSense using sentence-transformers.
Model: all-MiniLM-L6-v2 (384 dimensions) — tiny, fast, low RAM, runs locally.
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
        import torch
        from sentence_transformers import SentenceTransformer
        
        # Limit CPU threads to prevent hanging the whole server on Hetzner CX22
        torch.set_num_threads(2)
        
        # all-MiniLM-L6-v2: 384 dimensions, much better retrieval quality than MiniLM-L3
        # Still CPU-friendly (~90MB), semantic similarity benchmarks significantly higher
        model = SentenceTransformer("all-MiniLM-L6-v2")
        
        try:
            # Apply dynamic quantization to make it faster and lighter in RAM on CPU
            _transformer_model = torch.quantization.quantize_dynamic(
                model, {torch.nn.Linear}, dtype=torch.qint8
            )
            logger.info("sentence-transformers model loaded and quantized (int8)")
        except Exception as e:
            logger.warning(f"Quantization failed, using standard model: {e}")
            _transformer_model = model
            
    return _transformer_model


def embed_texts(texts):
    """
    Generate embeddings using sentence-transformers.
    Returns list of 384-dim float lists.
    """
    if not texts:
        return []

    model = _get_model()
    # Explicitly set batch_size=16 for processing on multicore CPUs
    vectors = model.encode(texts, batch_size=16, normalize_embeddings=True)
    # Convert numpy arrays to plain Python lists for pgvector
    return [v.tolist() for v in vectors]


def embed_texts_batched(texts, batch_size=16):
    """
    Generate embeddings in batches — useful for large textbook ingestion.
    Returns list of 384-dim float lists.
    """
    if not texts:
        return []

    all_vectors = []
    for i in range(0, len(texts), batch_size):
        batch = texts[i:i + batch_size]
        vectors = embed_texts(batch)
        if vectors:
            all_vectors.extend(vectors)
    return all_vectors