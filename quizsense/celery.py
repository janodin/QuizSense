"""
Celery app for QuizSense.
Run worker: celery -A quizsense worker --loglevel=info
"""
import os
from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "quizsense.settings")

app = Celery("quizsense")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()
