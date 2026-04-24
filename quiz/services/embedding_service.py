from . import gemini_service

def embed_texts(texts):
    """
    Generate embeddings using Gemini API via gemini_service.
    """
    if not texts:
        return []

    return gemini_service.embed_texts(texts)
