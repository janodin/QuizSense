from collections import Counter
from typing import List

import numpy as np

from ..models import TextbookChunk, UploadedChunk
from .chunking_service import split_text_into_chunks
from .embedding_service import embed_texts


def _cosine_similarity(a: List[float], b: List[float]) -> float:
    """Compute cosine similarity between two embedding vectors."""
    a = np.array(a, dtype=np.float32)
    b = np.array(b, dtype=np.float32)
    norm_a = np.linalg.norm(a)
    norm_b = np.linalg.norm(b)
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return float(np.dot(a, b) / (norm_a * norm_b))


def ingest_uploaded_file_chunks(uploaded_file):
    chunks = split_text_into_chunks(uploaded_file.extracted_text)
    if not chunks:
        return 0

    embeddings = embed_texts(chunks)
    if len(embeddings) != len(chunks):
        raise ValueError(f"Embedding mismatch: expected {len(chunks)}, got {len(embeddings)}.")

    chunk_objects = [
        UploadedChunk(
            upload_session=uploaded_file.upload_session,
            uploaded_file=uploaded_file,
            chapter=uploaded_file.chapter,
            chunk_index=index,
            content=chunk_text,
            embedding=embeddings[index],
        )
        for index, chunk_text in enumerate(chunks)
    ]
    UploadedChunk.objects.bulk_create(chunk_objects)
    return len(chunk_objects)


def _build_query_text(upload_session):
    chunks = UploadedChunk.objects.filter(upload_session=upload_session).order_by('id')[:6]
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


def retrieve_context_for_session(upload_session, mode='quiz', quiz_top_k=8, summary_top_k=12):
    query_text = _build_query_text(upload_session)
    if not query_text.strip():
        return {
            'context_text': '',
            'cross_reference_notes': 'No upload content was available to retrieve from.',
            'session_chunks': [],
            'textbook_chunks': [],
        }

    embeddings = embed_texts([query_text[:6000]])
    if not embeddings:
        raise ValueError("AI embedding service failed to return vectors.")
    query_embedding = embeddings[0]

    if mode == 'summary':
        session_k = 7
        textbook_k = 5
        top_k = summary_top_k
    else:
        session_k = 5
        textbook_k = 3
        top_k = quiz_top_k

    # Fetch all session chunks and rank by cosine similarity
    all_session_chunks = list(
        UploadedChunk.objects.filter(upload_session=upload_session).only('id', 'content', 'embedding')
    )
    session_chunk_ids = [(c.id, c) for c in all_session_chunks if c.embedding]
    scored = sorted(
        [(c[0], _cosine_similarity(query_embedding, c[1].embedding), c[1]) for c in session_chunk_ids],
        key=lambda x: x[1], reverse=True
    )
    session_chunks = [x[2] for x in scored[:max(top_k, session_k)]]

    # Fetch all textbook chunks for this chapter and rank by cosine similarity
    all_textbook_chunks = list(
        TextbookChunk.objects.filter(chapter=upload_session.chapter).only('id', 'content', 'embedding', 'topic')
    )
    textbook_chunk_ids = [(c.id, c) for c in all_textbook_chunks if c.embedding]
    scored_tb = sorted(
        [(c[0], _cosine_similarity(query_embedding, c[1].embedding), c[1]) for c in textbook_chunk_ids],
        key=lambda x: x[1], reverse=True
    )
    textbook_chunks = [x[2] for x in scored_tb[:max(top_k, textbook_k)]]

    session_chunks = session_chunks[:session_k]
    textbook_chunks = textbook_chunks[:textbook_k]

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
