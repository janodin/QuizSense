"""
Async tasks for QuizSense.
Run worker: celery -A quizsense worker --loglevel=info

Tasks:
- process_upload_session_task: Upload → extract → chunk → embed → summary
- generate_quiz_task: Session → RAG → quiz generation
- generate_recommendations_task: Attempt → AI recommendations
"""
import logging
from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3, default_retry_delay=30, time_limit=600, soft_time_limit=540)
def process_upload_session_task(self, upload_session_id):
    """
    Upload processing: extract text → chunk → embed → generate summary.
    Uses simplified sequential pipeline.
    """
    from django.db import close_old_connections, connection
    from django.db.utils import OperationalError
    from .services.pipeline_service import process_upload_session_simple

    close_old_connections()

    try:
        process_upload_session_simple(upload_session_id)
    except OperationalError as db_exc:
        logger.warning(
            "Upload session DB connection dropped for session %s: %s. Retrying...",
            upload_session_id,
            db_exc,
        )
        connection.close()
        if self.request.retries < self.max_retries:
            raise self.retry(exc=db_exc, countdown=10)
        raise
    except Exception as exc:
        logger.error("Upload session %s processing failed: %s", upload_session_id, exc)
        if self.request.retries < self.max_retries:
            raise self.retry(exc=exc)
        raise
    finally:
        connection.close()


@shared_task(bind=True, max_retries=3, default_retry_delay=30, time_limit=300, soft_time_limit=270)
def generate_quiz_task(self, upload_session_id):
    """
    Generate quiz for an upload session after summary is ready.
    Uses RAG to retrieve context and generates 10 MCQs via AI.
    """
    from django.db import close_old_connections, connection
    from django.db.utils import OperationalError
    from .models import Quiz, UploadSession
    from .services.pipeline_service import _process_quiz_for_session

    close_old_connections()

    try:
        result = _process_quiz_for_session(upload_session_id)
        if result.success:
            logger.info(
                "Quiz generated for session %s (duration=%.1fms)",
                upload_session_id,
                result.duration_ms,
            )
        else:
            # CRITICAL FIX: Mark quiz as FAILED so the polling page stops spinning
            logger.warning(
                "Quiz generation for session %s failed: %s",
                upload_session_id,
                result.error,
            )
            try:
                close_old_connections()
                upload_session = UploadSession.objects.get(id=upload_session_id)
                quiz = Quiz.objects.filter(upload_session=upload_session).order_by('-created_at').first()
                if quiz and quiz.status == Quiz.STATUS_PROCESSING:
                    quiz.status = Quiz.STATUS_FAILED
                    quiz.error_message = (result.error or "Quiz generation failed")[:500]
                    quiz.save(update_fields=["status", "error_message"])
            except Exception as inner_exc:
                logger.error("Failed to update quiz FAILED status for session %s: %s", upload_session_id, inner_exc)
    except OperationalError as db_exc:
        # DB connection dropped (idle timeout). Close stale conn and retry fast.
        logger.warning(
            "Quiz DB connection dropped for session %s: %s. Retrying...",
            upload_session_id,
            db_exc,
        )
        connection.close()
        if self.request.retries < self.max_retries:
            raise self.retry(exc=db_exc, countdown=10)
        raise
    except Exception as exc:
        logger.error("Quiz generation failed for session %s: %s", upload_session_id, exc)
        try:
            close_old_connections()
            upload_session = UploadSession.objects.get(id=upload_session_id)
            quiz = Quiz.objects.filter(upload_session=upload_session).order_by('-created_at').first()
            if quiz:
                quiz.status = Quiz.STATUS_FAILED
                quiz.error_message = str(exc)[:500]
                quiz.save(update_fields=["status", "error_message"])
        except Exception as inner_exc:
            logger.error("Failed to update quiz status for session %s: %s", upload_session_id, inner_exc)
        if self.request.retries < self.max_retries:
            raise self.retry(exc=exc)
        raise
    finally:
        connection.close()


@shared_task(bind=True, max_retries=3, default_retry_delay=30, time_limit=180, soft_time_limit=150)
def generate_recommendations_task(self, attempt_id):
    """
    Generate AI study recommendations for a completed quiz attempt.
    """
    from django.db import close_old_connections, connection
    from django.db.utils import OperationalError
    from .models import QuizAttempt
    from .services.pipeline_service import generate_recommendations_for_attempt

    close_old_connections()

    try:
        result = generate_recommendations_for_attempt(attempt_id)
        logger.info(
            "Recommendations for attempt %s (success=%s, duration=%.1fms)",
            attempt_id,
            result.success,
            result.duration_ms,
        )
        if not result.success:
            logger.warning(
                "Recommendations for attempt %s failed: %s",
                attempt_id,
                result.error,
            )
            try:
                close_old_connections()
                attempt = QuizAttempt.objects.get(id=attempt_id)
                if attempt.recommendation_status != QuizAttempt.RECOMMENDATION_COMPLETED:
                    attempt.recommendation_status = QuizAttempt.RECOMMENDATION_FAILED
                    attempt.recommendation_error = (result.error or "Recommendations generation failed")[:500]
                    attempt.save(update_fields=["recommendation_status", "recommendation_error"])
            except Exception as inner_exc:
                logger.error("Failed to update recommendation FAILED status for attempt %s: %s", attempt_id, inner_exc)
    except OperationalError as db_exc:
        # DB connection dropped (idle timeout). Close stale conn and retry fast.
        logger.warning(
            "Recommendations DB connection dropped for attempt %s: %s. Retrying...",
            attempt_id,
            db_exc,
        )
        connection.close()
        if self.request.retries < self.max_retries:
            raise self.retry(exc=db_exc, countdown=10)
        raise
    except Exception as exc:
        logger.error(
            "Recommendations failed for attempt %s: %s",
            attempt_id,
            exc,
        )
        connection.close()
        try:
            attempt = QuizAttempt.objects.get(id=attempt_id)
            attempt.recommendation_status = QuizAttempt.RECOMMENDATION_FAILED
            attempt.recommendation_error = str(exc)[:500]
            attempt.save(update_fields=["recommendation_status", "recommendation_error"])
        except Exception as inner_exc:
            logger.error("Failed to update recommendation FAILED status for attempt %s: %s", attempt_id, inner_exc)

        if self.request.retries < self.max_retries:
            raise self.retry(exc=exc)
        raise
    finally:
        connection.close()
