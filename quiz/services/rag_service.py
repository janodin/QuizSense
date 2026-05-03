"""
RAG (Retrieval-Augmented Generation) service for QuizSense.

RAM optimization for Hetzner CX22 (4GB):
- Textbook chunks are never all loaded into RAM at once.
  We fetch chunk IDs+embeddings in pages and score them in mini-batches.
- Session chunks are bounded (< 100 typically) so we load them directly.
- np.dot on float32 arrays is fast; page size is tuned to keep peak memory < 50 MB
  per scoring pass.
"""

from collections import Counter
from typing import List, Generator, Tuple

import numpy as np

from ..models import TextbookChunk, UploadedChunk
from .chunking_service import split_text_into_chunks
from .embedding_service import embed_texts_batched


# ─── Chunk scoring constants ──────────────────────────────────────────────────
_TEXTBOOK_PAGE_SIZE = 200   # chunk records per DB fetch (ID + embedding only)
_SESSION_CHUNK_LIMIT = 100  # safety cap for session chunks (normally < 50)


# ─── Query building ────────────────────────────────────────────────────────────

def _build_query_text(upload_session):
    chunks = list(
        UploadedChunk.objects.filter(upload_session=upload_session)
        .order_by('id')
        [:6]
    )
    if chunks:
        return "\n\n".join(chunk.content for chunk in chunks)

    files = upload_session.files.order_by('id')
    combined = "\n\n".join(file_obj.extracted_text for file_obj in files if file_obj.extracted_text)
    return combined[:6000]


def _cross_reference_textbook(textbook_chunks):
    topic_counter = Counter()
    for chunk in textbook_chunks:
        topic_name = chunk.topic.title if chunk.topic else chunk.source_title
        topic_counter[topic_name] += 1

    if not topic_counter:
        return "No textbook topic matches found."

    top_topics = topic_counter.most_common(5)
    topic_lines = [f"- {topic} ({count} matches)" for topic, count in top_topics]
    return "Top textbook matches:\n" + "\n".join(topic_lines)


# ─── Memory-bounded cosine similarity scoring ─────────────────────────────────

def _score_chunks_in_batches(
    query_np: np.ndarray,
    chunk_records: List,
    page_size: int = _TEXTBOOK_PAGE_SIZE,
) -> Generator[Tuple[int, float], None, None]:
    """
    Score a large list of chunk records against a query vector WITHOUT loading
    all embeddings into a single numpy array.

    Yields (chunk_id, cosine_similarity) tuples for all chunks, highest first.
    """
    # Process in small pages to keep peak numpy memory bounded.
    for start in range(0, len(chunk_records), page_size):
        page = chunk_records[start:start + page_size]
        ids = [c.id for c in page]
        embeddings = np.array(
            [c.embedding for c in page], dtype=np.float32
        )
        # Cosine similarity via normalized vectors: sim = dot(a, b)
        # Both query and stored vectors are L2-normalized by embed_texts*.
        norms = np.linalg.norm(embeddings, axis=1)
        norms[norms == 0] = 1.0
        similarities = np.dot(embeddings, query_np) / norms
        del embeddings
        for chunk_id, sim in zip(ids, similarities):
            yield chunk_id, float(sim)


def _get_top_k_chunks(query_np, chunks, top_k):
    """
    Return top_k chunks by cosine similarity.
    Works for both in-memory (session) and paged (textbook) chunk lists.
    """
    if not chunks:
        return []
    ids_scores = sorted(
        _score_chunks_in_batches(query_np, chunks),
        key=lambda x: x[1],
        reverse=True,
    )
    top_ids = [id_ for id_, _ in ids_scores[:top_k]]
    id_to_chunk = {c.id: c for c in chunks}
    return [id_to_chunk[id_] for id_ in top_ids if id_ in id_to_chunk]


# ─── Main retrieval API ───────────────────────────────────────────────────────

def retrieve_context_for_session(upload_session, mode='quiz', quiz_top_k=8, summary_top_k=12):
    query_text = _build_query_text(upload_session)
    if not query_text.strip():
        return {
            'context_text': '',
            'cross_reference_notes': 'No upload content was available to retrieve from.',
            'session_chunks': [],
            'textbook_chunks': [],
        }

    # Single short query — one embed_texts call (no batching needed).
    embeddings = embed_texts_batched([query_text[:6000]], batch_size=1)
    if not embeddings:
        raise ValueError("AI embedding service failed to return vectors.")

    query_np = np.array(embeddings[0], dtype=np.float32)
    del embeddings
    import gc as gcmod
    gcmod.collect()

    if mode == 'summary':
        session_k = 7
        textbook_k = 5
        top_k = summary_top_k
    else:
        session_k = 5
        textbook_k = 3
        top_k = quiz_top_k

    # ── Session chunks (normally small — load directly) ──────────────────────
    raw_session_chunks = list(
        UploadedChunk.objects.filter(upload_session=upload_session)
        .only('id', 'content', 'embedding')
        [:_SESSION_CHUNK_LIMIT]
    )
    valid_session_chunks = [c for c in raw_session_chunks if c.embedding]
    session_chunks = _get_top_k_chunks(
        query_np, valid_session_chunks, max(top_k, session_k)
    )[:session_k]

    # ── Textbook chunks (sample subset to avoid loading thousands) ───────────
    # We only need top_k matches — loading 500 chunks is more than enough.
    textbook_records = list(
        TextbookChunk.objects.filter(
            chapter=upload_session.chapter
        )
        .only('id', 'content', 'embedding', 'topic')
        .order_by('?')[:500]  # random sample for diversity
    )
    valid_textbook_chunks = [c for c in textbook_records if c.embedding]
    textbook_chunks = _get_top_k_chunks(
        query_np, valid_textbook_chunks, max(top_k, textbook_k)
    )[:textbook_k]

    del query_np
    gcmod.collect()

    context_lines = [
        '[Session Upload Context]',
        *[f"- {chunk.content}" for chunk in session_chunks],
        '',
        '[Textbook Reference Context]',
        *[f"- {chunk.content}" for chunk in textbook_chunks],
    ]

    return {
        'context_text': "\n".join(context_lines).strip(),
        'cross_reference_notes': _cross_reference_textbook(textbook_chunks),
        'session_chunks': session_chunks,
        'textbook_chunks': textbook_chunks,
    }
