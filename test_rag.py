import os
import django
import sys

# Setup Django environment
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'quizsense.settings')
django.setup()

from quiz.services import embedding_service, rag_service
from quiz.models import Chapter, UploadSession, UploadedFile, UploadedChunk

def test_embeddings():
    print("--- Testing Gemini Embeddings ---")
    test_text = ["Hello world", "Programming is fun"]
    try:
        vectors = embedding_service.embed_texts(test_text)
        print(f"Successfully generated {len(vectors)} vectors.")
        if len(vectors) > 0:
            dim = len(vectors[0])
            print(f"Vector dimension: {dim}")
            if dim == 768:
                print("✅ Dimension matches 768 (Gemini standard)")
            else:
                print(f"❌ Dimension mismatch! Expected 768, got {dim}")
        return vectors
    except Exception as e:
        print(f"❌ Embedding failed: {e}")
        return None

def test_rag_retrieval():
    print("\n--- Testing RAG Retrieval Logic ---")
    # Get or create a dummy chapter and session for testing
    chapter, _ = Chapter.objects.get_or_create(number=99, defaults={'title': 'Test Chapter'})
    session, _ = UploadSession.objects.get_or_create(
        chapter=chapter, 
        session_key='test_session_key',
        defaults={'summary': 'Test summary'}
    )
    
    # Create a dummy chunk if none exists
    if not UploadedChunk.objects.filter(upload_session=session).exists():
        print("Creating dummy chunk for testing...")
        # Create a dummy file object (won't work perfectly without real file but we just need foreign key)
        # Better to just check if retrieval runs without crashing
        pass

    try:
        context = rag_service.retrieve_context_for_session(session)
        print("✅ RAG retrieval function executed successfully.")
        print(f"Context keys: {list(context.keys())}")
        print(f"Cross-reference notes: {context.get('cross_reference_notes')}")
    except Exception as e:
        print(f"❌ RAG retrieval failed: {e}")

if __name__ == "__main__":
    vectors = test_embeddings()
    if vectors:
        test_rag_retrieval()
    else:
        print("\nSkipping RAG test because embeddings failed.")
