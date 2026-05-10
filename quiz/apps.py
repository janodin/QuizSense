from django.apps import AppConfig


class QuizConfig(AppConfig):
    name = "quiz"

    # Embeddings are generated through the configured AI provider API, so this
    # app intentionally does not pre-warm or load a local embedding model.
