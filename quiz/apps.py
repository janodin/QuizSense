from django.apps import AppConfig


class QuizConfig(AppConfig):
    name = "quiz"

    # NOTE: The embedding model is loaded lazily on first use via
    # _get_model() and is automatically evicted after IDLE_TIMEOUT_SECONDS
    # (default 120 s) of inactivity.  Do NOT pre-warm it here — pre-warming
    # keeps a ~500 MB model resident in every Gunicorn worker for the entire
    # lifetime of the worker, which is a primary cause of OOM on the
    # Hetzner CX22 (4 GB RAM).
