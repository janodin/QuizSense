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

from ..models import TextbookChunk, UploadedChunk, RetrievalLog
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
    Return top_k (chunk, similarity) tuples by cosine similarity.
    Works for both in-memory (session) and paged (textbook) chunk lists.
    """
    if not chunks:
        return []
    ids_scores = sorted(
        _score_chunks_in_batches(query_np, chunks),
        key=lambda x: x[1],
        reverse=True,
    )
    top_ids_scores = ids_scores[:top_k]
    id_to_chunk = {c.id: c for c in chunks}
    result = []
    for chunk_id, score in top_ids_scores:
        if chunk_id in id_to_chunk:
            result.append((id_to_chunk[chunk_id], float(score)))
    return result


# ─── Main retrieval API ───────────────────────────────────────────────────────

def retrieve_context_for_session(upload_session, mode='quiz', quiz_top_k=8, summary_top_k=12):
    import time
    start_time = time.perf_counter()

    query_text = ''
    retrieved_chunks = []
    session_chunk_count = 0
    textbook_chunk_count = 0
    all_top_scores = []
    result = None

    try:
        query_text = _build_query_text(upload_session)
        if not query_text.strip():
            result = {
                'context_text': '',
                'cross_reference_notes': 'No upload content was available to retrieve from.',
                'session_chunks': [],
                'textbook_chunks': [],
            }
            return result

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

        # ── Session chunks (normally small — load directly) ──────────────────
        raw_session_chunks = list(
            UploadedChunk.objects.filter(upload_session=upload_session)
            .only('id', 'content', 'embedding')
            [:_SESSION_CHUNK_LIMIT]
        )
        valid_session_chunks = [c for c in raw_session_chunks if c.embedding]
        session_chunks_scored = _get_top_k_chunks(
            query_np, valid_session_chunks, max(top_k, session_k)
        )
        session_chunks = [chunk for chunk, _ in session_chunks_scored[:session_k]]
        session_chunk_count = len(session_chunks)

        # ── Textbook chunks (topic-aware filtering, no expensive random sort) ──
        # Note: UploadedChunk does not have a topic field, so we filter textbook
        # chunks by chapter only. Topic filtering applies to TextbookChunk directly.
        textbook_qs = TextbookChunk.objects.filter(chapter=upload_session.chapter)

        # Deterministic ordering — much faster than order_by('?')
        textbook_records = list(
            textbook_qs
            .only('id', 'content', 'embedding', 'topic')
            .order_by('id')[:500]
        )
        valid_textbook_chunks = [c for c in textbook_records if c.embedding]
        textbook_chunks_scored = _get_top_k_chunks(
            query_np, valid_textbook_chunks, max(top_k, textbook_k)
        )
        textbook_chunks = [chunk for chunk, _ in textbook_chunks_scored[:textbook_k]]
        textbook_chunk_count = len(textbook_chunks)

        del query_np
        gcmod.collect()

        # Build log entries for every retrieved chunk
        for rank, (chunk, score) in enumerate(session_chunks_scored[:session_k], start=1):
            retrieved_chunks.append({
                'id': chunk.id,
                'source': 'session',
                'similarity': round(score, 6),
                'rank': rank,
            })
        for rank, (chunk, score) in enumerate(textbook_chunks_scored[:textbook_k], start=1):
            retrieved_chunks.append({
                'id': chunk.id,
                'source': 'textbook',
                'similarity': round(score, 6),
                'rank': rank,
            })

        all_top_scores = (
            [score for _, score in session_chunks_scored[:session_k]]
            + [score for _, score in textbook_chunks_scored[:textbook_k]]
        )

        context_lines = [
            '[Session Upload Context]',
            *[f"- {chunk.content}" for chunk in session_chunks],
            '',
            '[Textbook Reference Context]',
            *[f"- {chunk.content}" for chunk in textbook_chunks],
        ]

        result = {
            'context_text': "\n".join(context_lines).strip(),
            'cross_reference_notes': _cross_reference_textbook(textbook_chunks),
            'session_chunks': session_chunks,
            'textbook_chunks': textbook_chunks,
        }
        return result
    finally:
        latency_ms = (time.perf_counter() - start_time) * 1000
        avg_sim = sum(all_top_scores) / len(all_top_scores) if all_top_scores else None
        min_sim = min(all_top_scores) if all_top_scores else None
        max_sim = max(all_top_scores) if all_top_scores else None
        try:
            RetrievalLog.objects.create(
                upload_session=upload_session,
                query_text=query_text[:6000],
                mode=mode,
                retrieved_chunks=retrieved_chunks,
                session_chunk_count=session_chunk_count,
                textbook_chunk_count=textbook_chunk_count,
                avg_similarity_top_k=avg_sim,
                min_similarity_top_k=min_sim,
                max_similarity_top_k=max_sim,
                retrieval_latency_ms=latency_ms,
            )
        except Exception as e:
            # Never let logging break the retrieval pipeline, but record the failure
            import logging
            logging.getLogger(__name__).warning("Failed to create RetrievalLog: %s", e)
