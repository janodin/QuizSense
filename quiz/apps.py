from django.apps import AppConfig
import threading


class QuizConfig(AppConfig):
    name = "quiz"

    def ready(self):
        # Pre-warm the embedding model in a background thread so the
        # first request doesn't pay the 30-60s model-loading cost.
        # On Render (free tier) workers are killed after requests,
        # so every new worker pays this cost — but at least it won't
        # timeout the user's request.
        t = threading.Thread(target=self._prewarm_model, daemon=True)
        t.start()

    @staticmethod
    def _prewarm_model():
        from quiz.services.embedding_service import _get_model
        _get_model()
