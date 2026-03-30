from django.conf import settings


_model = None


def _get_model():
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer
        _model = SentenceTransformer(settings.SENTENCE_TRANSFORMER_MODEL)
    return _model


def embed_texts(texts):
    if not texts:
        return []

    model = _get_model()
    vectors = model.encode(
        texts,
        normalize_embeddings=True,
        show_progress_bar=False,
    )
    return vectors.tolist()
