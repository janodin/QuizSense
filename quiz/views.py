from django.shortcuts import render, redirect, get_object_or_404
from django.http import HttpResponseForbidden, JsonResponse
from django.contrib import messages
from django.utils import timezone
from .models import Chapter, Topic, UploadSession, UploadedFile, Question, Quiz, QuizAttempt, QuizAnswer
from .forms import MultiFileUploadForm
from .services.pipeline_service import (
    queue_upload_session_processing,
    queue_quiz_generation,
)


def _get_session_key(request):
    """Return the current session key, creating one if needed."""
    if not request.session.session_key:
        request.session.create()
    return request.session.session_key


def _check_ownership(request, obj, owner_field='session_key'):
    """
    Verify the current session owns the object.
    Returns True if owned, False otherwise.
    """
    owner_value = getattr(obj, owner_field, None)
    current_key = _get_session_key(request)
    return owner_value == current_key


def home(request):
    form = MultiFileUploadForm()

    if request.method == 'POST':
        form = MultiFileUploadForm(request.POST, request.FILES)
        if form.is_valid():
            chapter = form.cleaned_data['chapter']
            files = request.FILES.getlist('files')

            if not request.session.session_key:
                request.session.create()

            upload_session = UploadSession.objects.create(
                chapter=chapter,
                session_key=request.session.session_key or '',
            )

            try:
                for file_obj in files:
                    filename = file_obj.name.lower()
                    if filename.endswith('.pdf'):
                        file_type = 'pdf'
                    elif filename.endswith('.docx'):
                        file_type = 'docx'
                    else:
                        continue

                    UploadedFile.objects.create(
                        upload_session=upload_session,
                        chapter=chapter,
                        file=file_obj,
                        file_type=file_type,
                    )

                if not upload_session.files.exists():
                    upload_session.delete()
                    messages.error(request, "Please upload at least one supported PDF or DOCX file.")
                    return render(request, 'quiz/home.html', {'form': form})

                queue_upload_session_processing(upload_session.id)

            except Exception as e:
                import logging
                logger = logging.getLogger(__name__)
                logger.exception("Upload session processing failed")
                upload_session.delete()
                messages.error(request, "An error occurred during processing. Please try again or contact support.")
                return render(request, 'quiz/home.html', {'form': form})

            return redirect('study_summary', upload_session_id=upload_session.id)

    return render(request, 'quiz/home.html', {'form': form})


def study_summary(request, upload_session_id):
    upload_session = get_object_or_404(
        UploadSession.objects.prefetch_related('files'), id=upload_session_id
    )
    if not _check_ownership(request, upload_session):
        return HttpResponseForbidden("You do not have access to this study summary.")
    session_files = upload_session.files.order_by('uploaded_at')
    
    # Calculate text quality indicators for each file
    file_quality_data = []
    total_chars = 0
    total_words = 0
    
    for file_obj in session_files:
        text = file_obj.extracted_text or ""
        char_count = len(text)
        word_count = len(text.split())
        
        # Determine quality level based on extracted content
        if char_count == 0:
            quality = 'failed'
            quality_label = 'Failed'
            quality_class = 'danger'
            quality_icon = 'x-circle-fill'
        elif char_count < 100:
            quality = 'poor'
            quality_label = 'Poor'
            quality_class = 'warning'
            quality_icon = 'exclamation-triangle-fill'
        elif char_count < 500:
            quality = 'fair'
            quality_label = 'Fair'
            quality_class = 'info'
            quality_icon = 'info-circle-fill'
        else:
            quality = 'good'
            quality_label = 'Good'
            quality_class = 'success'
            quality_icon = 'check-circle-fill'
        
        file_quality_data.append({
            'file': file_obj,
            'char_count': char_count,
            'word_count': word_count,
            'quality': quality,
            'quality_label': quality_label,
            'quality_class': quality_class,
            'quality_icon': quality_icon,
        })
        
        total_chars += char_count
        total_words += word_count
    
    # Overall session quality
    if total_chars == 0:
        overall_quality = 'failed'
        overall_quality_label = 'No text extracted'
        overall_quality_class = 'danger'
    elif total_chars < 500:
        overall_quality = 'poor'
        overall_quality_label = 'Limited content'
        overall_quality_class = 'warning'
    elif total_chars < 2000:
        overall_quality = 'fair'
        overall_quality_label = 'Moderate content'
        overall_quality_class = 'info'
    else:
        overall_quality = 'good'
        overall_quality_label = 'Rich content'
        overall_quality_class = 'success'
    
    return render(request, 'quiz/summary.html', {
        'upload_session': upload_session,
        'session_files': session_files,
        'file_quality_data': file_quality_data,
        'total_chars': total_chars,
        'total_words': total_words,
        'overall_quality': overall_quality,
        'overall_quality_label': overall_quality_label,
        'overall_quality_class': overall_quality_class,
    })


def upload_session_status(request, upload_session_id):
    upload_session = get_object_or_404(UploadSession, id=upload_session_id)
    if not _check_ownership(request, upload_session):
        return HttpResponseForbidden("You do not have access to this upload session.")

    return JsonResponse({
        'status': upload_session.processing_status,
        'error': upload_session.processing_error,
        'summary_ready': bool(upload_session.summary.strip()),
    })


def generate_quiz(request, upload_session_id):
    upload_session = get_object_or_404(UploadSession, id=upload_session_id)
    if not _check_ownership(request, upload_session):
        return HttpResponseForbidden("You do not have access to this upload session.")

    if upload_session.processing_status in {UploadSession.STATUS_PENDING, UploadSession.STATUS_PROCESSING}:
        messages.info(request, "Your files are still being processed. Please wait for the study summary to finish loading.")
        return redirect('study_summary', upload_session_id=upload_session.id)

    if upload_session.processing_status == UploadSession.STATUS_FAILED:
        messages.error(
            request,
            upload_session.processing_error or "This upload session failed during processing. Please upload the files again.",
        )
        return redirect('study_summary', upload_session_id=upload_session.id)

    chapter = upload_session.chapter
    if not chapter:
        messages.error(request, "No chapter was associated with this upload session.")
        return redirect('home')

    if not upload_session.files.filter(extracted_text__gt='').exists():
        messages.error(request, "No text could be extracted from the uploaded files.")
        return redirect('home')

    existing_quiz = Quiz.objects.filter(upload_session=upload_session, status=Quiz.STATUS_COMPLETED).first()
    if existing_quiz:
        return redirect('take_quiz', quiz_id=existing_quiz.id)

    quiz = Quiz.objects.create(
        chapter=chapter,
        upload_session=upload_session,
        uploaded_file=upload_session.files.order_by('id').first(),
        status=Quiz.STATUS_PROCESSING,
    )

    queue_quiz_generation(upload_session_id)

    return redirect('quiz_waiting', quiz_id=quiz.id)


def quiz_waiting(request, quiz_id):
    """Show waiting page while quiz is being generated."""
    quiz = get_object_or_404(Quiz, id=quiz_id)
    if quiz.upload_session and not _check_ownership(request, quiz.upload_session):
        return HttpResponseForbidden("You do not have access to this quiz.")
    return render(request, 'quiz/quiz_waiting.html', {'quiz': quiz})


def quiz_status(request, quiz_id):
    """Return quiz generation status as JSON for polling."""
    quiz = get_object_or_404(Quiz, id=quiz_id)
    if quiz.upload_session and not _check_ownership(request, quiz.upload_session):
        return HttpResponseForbidden("You do not have access to this quiz.")

    return JsonResponse({
        'status': quiz.status,
        'error': quiz.error_message or None,
        'quiz_id': quiz.id,
    })


def take_quiz(request, quiz_id):
    quiz = get_object_or_404(Quiz, id=quiz_id)
    if quiz.upload_session and not _check_ownership(request, quiz.upload_session):
        return HttpResponseForbidden("You do not have access to this quiz.")
    if quiz.status != Quiz.STATUS_COMPLETED:
        messages.info(request, "This quiz is still being generated. Please wait.")
        return redirect('quiz_waiting', quiz_id=quiz.id)
    questions = list(quiz.questions.select_related('topic', 'chapter').all())
    return render(request, 'quiz/take_quiz.html', {'quiz': quiz, 'questions': questions})

def submit_quiz(request, quiz_id):
    if request.method != 'POST':
        return redirect('take_quiz', quiz_id=quiz_id)

    quiz = get_object_or_404(Quiz, id=quiz_id)
    if quiz.upload_session and not _check_ownership(request, quiz.upload_session):
        return HttpResponseForbidden("You do not have access to this quiz.")
    if quiz.status != Quiz.STATUS_COMPLETED:
        messages.error(request, "This quiz is not ready for submission.")
        return redirect('take_quiz', quiz_id=quiz_id)
    questions = list(quiz.questions.select_related('topic', 'chapter').all())

    if not request.session.session_key:
        request.session.create()

    valid_choices = {'A', 'B', 'C', 'D'}

    unanswered = []
    validated_answers = []
    for question in questions:
        selected = request.POST.get(f'q_{question.id}', '').strip().upper()
        if selected not in valid_choices:
            unanswered.append(question.id)
        validated_answers.append((question, selected))

    if unanswered:
        messages.error(
            request,
            f"Please answer all questions before submitting. "
            f"Missing answers for {len(unanswered)} question(s)."
        )
        return redirect('take_quiz', quiz_id=quiz_id)

    attempt = QuizAttempt.objects.create(
        quiz=quiz,
        session_key=request.session.session_key or '',
        total_questions=len(questions),
        recommendation_status=QuizAttempt.RECOMMENDATION_PROCESSING,
    )

    score = 0
    questions_with_answers = []

    for question, selected in validated_answers:
        is_correct = selected == question.correct_answer
        if is_correct:
            score += 1
        QuizAnswer.objects.create(
            attempt=attempt,
            question=question,
            selected_answer=selected,
            is_correct=is_correct,
        )
        questions_with_answers.append({
            'question': question.text,
            'correct_answer': question.correct_answer,
            'selected_answer': selected,
            'is_correct': is_correct,
            'topic': question.topic.title if question.topic else 'General',
        })

    attempt.score = score
    attempt.completed_at = timezone.now()
    attempt.save()

    try:
        from .services.pipeline_service import _process_recommendations_for_attempt
        result = _process_recommendations_for_attempt(attempt)
        if result.success:
            attempt.refresh_from_db()
        else:
            attempt.recommendation_status = QuizAttempt.RECOMMENDATION_FAILED
            attempt.recommendation_error = result.error or "Unknown error"
            attempt.save(update_fields=["recommendation_status", "recommendation_error"])
            messages.warning(request, "Quiz submitted, but study recommendations could not be generated.")
    except Exception as exc:
        import logging
        logger = logging.getLogger(__name__)
        logger.warning("Recommendation generation failed for attempt %s: %s", attempt.id, exc)
        attempt.recommendation_status = QuizAttempt.RECOMMENDATION_FAILED
        attempt.recommendation_error = str(exc)[:500]
        attempt.save(update_fields=["recommendation_status", "recommendation_error"])
        messages.warning(request, "Quiz submitted, but study recommendations could not be generated.")

    return redirect('quiz_results', attempt_id=attempt.id)


def quiz_results(request, attempt_id):
    attempt = get_object_or_404(QuizAttempt, id=attempt_id)
    if not _check_ownership(request, attempt):
        return HttpResponseForbidden("You do not have access to these results.")
    incorrect = attempt.total_questions - attempt.score
    
    context = {
        'attempt': attempt,
        'incorrect': incorrect,
    }
    return render(request, 'quiz/results.html', context)


def review_quiz(request, attempt_id):
    attempt = get_object_or_404(QuizAttempt, id=attempt_id)
    if not _check_ownership(request, attempt):
        return HttpResponseForbidden("You do not have access to this review.")
    answers = attempt.answers.select_related('question').order_by('id')
    return render(request, 'quiz/review.html', {'attempt': attempt, 'answers': answers})


