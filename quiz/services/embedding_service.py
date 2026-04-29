"""
Embedding service for QuizSense using the Minimax Cloud API.
Model: embo-01 (High-performance embeddings).
This service is cloud-based, saving local CPU and RAM.
"""

import os
import requests
import logging
import time

logger = logging.getLogger(__name__)

MINIMAX_API_KEY = os.getenv('MINIMAX_API_KEY')
MINIMAX_GROUP_ID = os.getenv('MINIMAX_GROUP_ID')
MINIMAX_EMBED_URL = "https://api.minimax.chat/v1/embeddings"

def embed_texts(texts, task_type="db"):
    """
    Generate embeddings using Minimax embo-01 model.
    task_type can be "db" (for storage) or "query" (for search).
    """
    if not texts:
        return []

    headers = {
        "Authorization": f"Bearer {MINIMAX_API_KEY}",
        "Content-Type": "application/json"
    }
    
    if MINIMAX_GROUP_ID:
        headers["Abab-Group-ID"] = MINIMAX_GROUP_ID
    
    payload = {
        "model": "embo-01",
        "texts": texts,
        "type": task_type
    }

    try:
        # Adding a small sleep to avoid rate limiting
        time.sleep(0.5)
        response = requests.post(MINIMAX_EMBED_URL, headers=headers, json=payload, timeout=60)
        response.raise_for_status()
        data = response.json()
        
        if data.get("base_resp", {}).get("status_code") != 0:
            logger.error(f"Minimax embedding error: {data.get('base_resp')}")
            return []
            
        return data.get("vectors", [])
    except Exception as e:
        logger.exception(f"Failed to fetch embeddings from Minimax: {str(e)}")
        return []

def embed_texts_batched(texts, batch_size=32):
    """
    Generate embeddings in batches — useful for large textbook ingestion.
    Returns list of 1536-dim float lists (Minimax embo-01 uses 1536 dims).
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
