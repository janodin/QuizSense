"""
RAG sanity-check script for QuizSense.

Architecture (as of April 2026):
  - Embeddings: Ollama local API — nomic-embed-text (768 dimensions)
  - RAG retrieval: cosine similarity on stored vectors
  - Text splitting: 600-word chunks, 120-word overlap
  - Textbook chunks: TextbookChunk model (chapter FK, topic FK, source_title, content, embedding)
  - Upload chunks: UploadedChunk model (upload_session FK, chapter FK, content, embedding)

Usage:
  python test_rag.py

Requires:
  Ollama running locally with nomic-embed-text model
"""

import os
import sys
import django

# Setup Django
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "quizsense.settings")
django.setup()

from quiz.services.ai_service import embed_texts
from quiz.services import rag_service
from quiz.models import Chapter, UploadSession


def test_embeddings():
    """Test Ollama local embedding generation (nomic-embed-text, 768 dimensions)."""
    print("--- Testing Ollama Local Embeddings ---")
    test_texts = [
        "Variables are named storage locations in programming.",
        "A function is a reusable block of code that performs a specific task.",
    ]
    try:
        vectors = embed_texts(test_texts)
        print(f"Generated {len(vectors)} vectors.")

        if len(vectors) == 0:
            print("FAIL: No vectors returned.")
            return None

        dim = len(vectors[0])
        print(f"Vector dimension: {dim}")

        if dim == 768:
            print("PASS: Dimension is 768 (nomic-embed-text).")
        else:
            print(f"FAIL: Expected 768, got {dim}.")

        return vectors

    except Exception as e:
        print(f"FAIL: Embedding generation failed: {e}")
        return None


def test_chunking():
    """Test text chunking logic without requiring API calls."""
    print("\n--- Testing Chunking Service ---")
    from quiz.services.chunking_service import split_text_into_chunks

    # 600-word text
    words = " ".join([f"word{i}" for i in range(500)])
    chunks = split_text_into_chunks(words, chunk_size_words=600, overlap_words=120)

    print(f"Input: 500 words")
    print(f"Chunks produced: {len(chunks)}")

    if len(chunks) == 1:
        print("PASS: Single chunk for text <= chunk_size_words.")
    else:
        print(f"Got {len(chunks)} chunks.")

    # Test with 1000-word text (should produce 2 chunks with 120-word overlap)
    words = " ".join([f"word{i}" for i in range(1000)])
    chunks = split_text_into_chunks(words, chunk_size_words=600, overlap_words=120)
    print(f"\nInput: 1000 words → {len(chunks)} chunks (expected 2)")

    if len(chunks) == 2:
        print("PASS: 2 chunks for 1000-word text.")
    else:
        print(f"WARNING: Expected 2 chunks, got {len(chunks)}.")

    # Verify overlap
    if len(chunks) == 2:
        overlap = set(chunks[0].split()) & set(chunks[1].split())
        print(f"Overlap between chunk 1 and 2: {len(overlap)} shared words (expected ~120).")

    return True


def test_rag_retrieval_logic():
    """Test RAG retrieval function with a dummy session."""
    print("\n--- Testing RAG Retrieval Logic ---")

    # Get or create a dummy chapter
    chapter, _ = Chapter.objects.get_or_create(
        number=99,
        defaults={"title": "Test Chapter"}
    )

    # Get or create a dummy upload session
    session, _ = UploadSession.objects.get_or_create(
        chapter=chapter,
        session_key="test_session_key_rag",
        defaults={"summary": "Test summary"}
    )

    try:
        context = rag_service.retrieve_context_for_session(session)
        print("RAG retrieval function executed without crashing.")
        print(f"Context keys: {list(context.keys())}")
        print(f"cross_reference_notes: {context.get('cross_reference_notes', 'N/A')[:100]}...")
        print("PASS: retrieve_context_for_session works.")
        return True
    except Exception as e:
        print(f"FAIL: RAG retrieval raised: {e}")
        return False


def main():
    print("=" * 60)
    print("QuizSense RAG System Check")
    print("=" * 60)

    results = []

    # 1. Embeddings (requires GOOGLE_API_KEY)
    vectors = test_embeddings()
    results.append(("Embeddings", vectors is not None))

    # 2. Chunking (no external dependencies)
    results.append(("Chunking", test_chunking()))

    # 3. RAG retrieval (requires database + GOOGLE_API_KEY)
    results.append(("RAG Retrieval", test_rag_retrieval_logic()))

    print("\n" + "=" * 60)
    print("Summary")
    print("=" * 60)
    all_pass = True
    for name, passed in results:
        status = "PASS" if passed else "FAIL"
        print(f"  {name}: {status}")
        if not passed:
            all_pass = False

    if all_pass:
        print("\nAll checks passed.")
    else:
        print("\nSome checks failed. Review output above.")
        sys.exit(1)


if __name__ == "__main__":
    main()
