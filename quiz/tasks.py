"""
Async tasks for QuizSense quiz generation.
Run worker: celery -A quizsense worker --loglevel=info
"""
import logging
from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=2, default_retry_delay=30)
def process_upload_session_task(self, upload_session_id):
    """
    Durable background task for upload extraction and summary generation.
    Falls back to the same service used by local threaded processing.
    """
    from .services.upload_processing import process_upload_session

    try:
        process_upload_session(upload_session_id)
    except Exception as exc:
        logger.error("Upload session %s processing task failed: %s", upload_session_id, exc)
        if self.request.retries < self.max_retries:
            raise self.retry(exc=exc)
        raise


@shared_task(bind=True, max_retries=2, default_retry_delay=30)
def generate_quiz_for_session_task(self, upload_session_id):
    """
    Generate the quiz connected to an upload session after its summary is ready.
    """
    from .services.learning_pipeline import generate_quiz_for_session

    try:
        generate_quiz_for_session(upload_session_id)
    except Exception as exc:
        logger.error("Upload session %s quiz generation task failed: %s", upload_session_id, exc)
        if self.request.retries < self.max_retries:
            raise self.retry(exc=exc)
        raise


@shared_task(bind=True, max_retries=2, default_retry_delay=30)
def generate_quiz_task(self, quiz_id):
    """
    Backward-compatible task entrypoint for older queued jobs.
    New code queues generate_quiz_for_session_task.
    """
    from .models import Quiz
    from .services.learning_pipeline import generate_quiz_for_session

    quiz = Quiz.objects.select_related('upload_session').get(id=quiz_id)
    if not quiz.upload_session_id:
        quiz.status = Quiz.STATUS_FAILED
        quiz.error_message = "Quiz has no associated upload session."
        quiz.save(update_fields=['status', 'error_message'])
        return

    try:
        generate_quiz_for_session(quiz.upload_session_id)
    except Exception as exc:
        logger.error("Quiz %s generation task failed: %s", quiz_id, exc)
        if self.request.retries < self.max_retries:
            raise self.retry(exc=exc)
        raise


@shared_task(bind=True, max_retries=2, default_retry_delay=30)
def generate_recommendations_task(self, attempt_id):
    """
    Celery task to asynchronously generate AI study recommendations for a quiz attempt.
    """
    from .models import QuizAttempt
    from .services.learning_pipeline import generate_recommendations_for_attempt

    try:
        generate_recommendations_for_attempt(attempt_id)
        logger.info(f"Recommendations generated for attempt {attempt_id}.")
    except Exception as exc:
        logger.error(f"Recommendations generation failed for attempt {attempt_id}: {exc}")
        attempt = QuizAttempt.objects.get(id=attempt_id)
        attempt.recommendation_status = QuizAttempt.RECOMMENDATION_FAILED
        attempt.recommendation_error = str(exc)
        attempt.save(update_fields=['recommendation_status', 'recommendation_error'])

        if self.request.retries < self.max_retries:
            raise self.retry(exc=exc)

        raise
