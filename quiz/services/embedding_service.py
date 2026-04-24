from . import minimax_service

def embed_texts(texts):
    """
    Generate embeddings using MiniMax API via minimax_service.
    """
    if not texts:
        return []

    return minimax_service.embed_texts(texts)
