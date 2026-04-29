from collections import Counter
from typing import List

import numpy as np

from ..models import TextbookChunk, UploadedChunk
from .chunking_service import split_text_into_chunks
from .embedding_service import embed_texts


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


def _get_top_k_chunks(query_np, chunks, top_k):
    if not chunks:
        return []
    embeddings_np = np.array([c.embedding for c in chunks], dtype=np.float32)
    norms = np.linalg.norm(embeddings_np, axis=1) * np.linalg.norm(query_np)
    norms[norms == 0] = 1.0
    similarities = np.dot(embeddings_np, query_np) / norms
    
    # Get top_k indices sorted descending
    top_indices = np.argsort(similarities)[-top_k:][::-1]
    return [chunks[i] for i in top_indices]


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
    
    query_np = np.array(embeddings[0], dtype=np.float32)

    if mode == 'summary':
        session_k = 7
        textbook_k = 5
        top_k = summary_top_k
    else:
        session_k = 5
        textbook_k = 3
        top_k = quiz_top_k

    # Fetch all session chunks
    all_session_chunks = list(
        UploadedChunk.objects.filter(upload_session=upload_session).only('id', 'content', 'embedding')
    )
    valid_session_chunks = [c for c in all_session_chunks if c.embedding]
    session_chunks = _get_top_k_chunks(query_np, valid_session_chunks, max(top_k, session_k))[:session_k]

    # Fetch all textbook chunks for this chapter
    all_textbook_chunks = list(
        TextbookChunk.objects.filter(chapter=upload_session.chapter).only('id', 'content', 'embedding', 'topic')
    )
    valid_textbook_chunks = [c for c in all_textbook_chunks if c.embedding]
    textbook_chunks = _get_top_k_chunks(query_np, valid_textbook_chunks, max(top_k, textbook_k))[:textbook_k]

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
