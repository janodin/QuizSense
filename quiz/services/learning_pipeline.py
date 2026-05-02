import logging
import threading

from django.db import transaction
from django.db import close_old_connections
from django.utils import timezone

from ..models import Question, Quiz, QuizAttempt, UploadSession
from .ai_service import generate_mcq_questions, generate_recommendations
from .topic_service import find_topic_for_chapter

logger = logging.getLogger(__name__)

QUIZ_CONTEXT_CHARS = 12000


def build_upload_context(upload_session, max_chars=QUIZ_CONTEXT_CHARS):
    files = upload_session.files.order_by('id')
    parts = [
        uploaded_file.extracted_text.strip()
        for uploaded_file in files
        if uploaded_file.extracted_text and uploaded_file.extracted_text.strip()
    ]
    return "\n\n".join(parts)[:max_chars]


def get_or_create_session_quiz(upload_session):
    quiz = Quiz.objects.filter(upload_session=upload_session).order_by('-created_at').first()
    if quiz:
        return quiz, False

    primary_file = upload_session.files.order_by('id').first()
    quiz = Quiz.objects.create(
        chapter=upload_session.chapter,
        upload_session=upload_session,
        uploaded_file=primary_file,
        status=Quiz.STATUS_PENDING,
    )
    return quiz, True


def queue_quiz_generation(upload_session_id):
    def dispatch():
        try:
            from ..tasks import generate_quiz_for_session_task
            generate_quiz_for_session_task.delay(upload_session_id)
        except Exception as exc:
            logger.warning("Celery unavailable for quiz generation on upload session %s: %s", upload_session_id, exc)
            worker = threading.Thread(
                target=_generate_quiz_thread,
                args=(upload_session_id,),
                daemon=True,
                name=f"quiz-generation-{upload_session_id}",
            )
            worker.start()

    transaction.on_commit(dispatch)


def _generate_quiz_thread(upload_session_id):
    close_old_connections()
    try:
        generate_quiz_for_session(upload_session_id)
    finally:
        close_old_connections()


def generate_quiz_for_session(upload_session_id):
    upload_session = UploadSession.objects.select_related('chapter').get(id=upload_session_id)
    if upload_session.processing_status != UploadSession.STATUS_COMPLETED:
        logger.info("Skipping quiz generation for upload session %s because summary is not ready.", upload_session_id)
        return None

    quiz, _ = get_or_create_session_quiz(upload_session)
    if quiz.status == Quiz.STATUS_COMPLETED and quiz.questions.exists():
        return quiz
    if quiz.status == Quiz.STATUS_GENERATING:
        return quiz

    quiz.status = Quiz.STATUS_GENERATING
    quiz.error_message = ''
    quiz.save(update_fields=['status', 'error_message'])

    try:
        context_text = build_upload_context(upload_session)
        if not context_text.strip():
            raise ValueError("No extracted upload text was available for quiz generation.")

        chapter = upload_session.chapter
        if not chapter:
            raise ValueError("Quiz has no associated chapter.")

        mcq_list = generate_mcq_questions(context_text, chapter.title)
        if not mcq_list:
            raise ValueError("The AI service did not return quiz questions.")

        with transaction.atomic():
            quiz.questions.clear()
            primary_file = upload_session.files.order_by('id').first()
            for mcq in mcq_list[:10]:
                choices = mcq.get('choices') or {}
                topic = find_topic_for_chapter(chapter, mcq.get('topic', ''), create=False)
                question = Question.objects.create(
                    chapter=chapter,
                    topic=topic,
                    uploaded_file=primary_file,
                    text=mcq['question'],
                    choice_a=choices['A'],
                    choice_b=choices['B'],
                    choice_c=choices['C'],
                    choice_d=choices['D'],
                    correct_answer=mcq['correct_answer'],
                )
                quiz.questions.add(question)

            quiz.status = Quiz.STATUS_COMPLETED
            quiz.generated_at = timezone.now()
            quiz.error_message = ''
            quiz.save(update_fields=['status', 'generated_at', 'error_message'])

        logger.info("Quiz %s generated for upload session %s with %s questions.", quiz.id, upload_session_id, len(mcq_list[:10]))
        return quiz
    except Exception as exc:
        logger.exception("Quiz generation failed for upload session %s", upload_session_id)
        quiz.status = Quiz.STATUS_FAILED
        quiz.error_message = str(exc)
        quiz.save(update_fields=['status', 'error_message'])
        raise


def generate_recommendations_for_attempt(attempt_id):
    attempt = QuizAttempt.objects.select_related('quiz', 'quiz__chapter').get(id=attempt_id)
    attempt.recommendation_status = QuizAttempt.RECOMMENDATION_GENERATING
    attempt.recommendation_error = ''
    attempt.save(update_fields=['recommendation_status', 'recommendation_error'])

    answers = attempt.answers.select_related('question', 'question__topic').all()
    questions_with_answers = [
        {
            'question': answer.question.text,
            'selected_answer': answer.selected_answer,
            'correct_answer': answer.question.correct_answer,
            'is_correct': answer.is_correct,
            'topic': answer.question.topic.title if answer.question.topic else 'General',
        }
        for answer in answers
    ]

    recommendation = generate_recommendations(attempt, questions_with_answers)
    attempt.ai_recommendation = recommendation
    attempt.recommendation_status = QuizAttempt.RECOMMENDATION_COMPLETED
    attempt.save(update_fields=['ai_recommendation', 'recommendation_status'])
    return attempt
