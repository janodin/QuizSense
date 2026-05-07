from django.shortcuts import render, redirect, get_object_or_404
from django.http import HttpResponseForbidden, JsonResponse
from django.contrib import messages
from django.utils import timezone
from django.db.models import Avg, Count, Q, F, StdDev, FloatField, Sum
from django.db.models.functions import Cast
from .models import Chapter, Topic, UploadSession, UploadedFile, Question, Quiz, QuizAttempt, QuizAnswer, TextbookChunk, UploadedChunk, GenerationMetric, RetrievalLog
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

    attempt.score = score
    attempt.completed_at = timezone.now()
    attempt.save()

    # Queue recommendations asynchronously so the user isn't blocked
    # waiting for the AI API call (10-30s) before seeing results.
    try:
        from .services.pipeline_service import queue_recommendations_generation
        queue_recommendations_generation(attempt.id)
    except Exception as exc:
        import logging
        logger = logging.getLogger(__name__)
        logger.warning("Failed to queue recommendations for attempt %s: %s", attempt.id, exc)
        attempt.recommendation_status = QuizAttempt.RECOMMENDATION_FAILED
        attempt.recommendation_error = str(exc)[:500]
        attempt.save(update_fields=["recommendation_status", "recommendation_error"])

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


def recommendation_status(request, attempt_id):
    """Return recommendation generation status as JSON for polling."""
    attempt = get_object_or_404(QuizAttempt, id=attempt_id)
    if not _check_ownership(request, attempt):
        return HttpResponseForbidden("You do not have access to this attempt.")

    return JsonResponse({
        'status': attempt.recommendation_status,
        'error': attempt.recommendation_error or None,
        'recommendation_html': attempt.ai_recommendation or None,
    })


def evaluation(request):
    """
    Pure Evaluation Dashboard.
    Research-oriented metrics for RAG retrieval quality and model generation quality.
    """
    # ─── RAG Retrieval Evaluation ─────────────────────────────────────────────
    total_retrieval_logs = RetrievalLog.objects.count()
    avg_retrieval_latency = RetrievalLog.objects.aggregate(avg=Avg('retrieval_latency_ms'))['avg']

    weighted = RetrievalLog.objects.exclude(avg_similarity_top_k=None).annotate(
        total_chunks=F('session_chunk_count') + F('textbook_chunk_count'),
        weighted_sim=Cast(F('avg_similarity_top_k') * F('total_chunks'), output_field=FloatField())
    ).aggregate(sum_w=Sum('weighted_sim'), sum_k=Sum('total_chunks'))

    avg_top_k_similarity = None
    if weighted['sum_k']:
        avg_top_k_similarity = weighted['sum_w'] / weighted['sum_k']

    cross_source_count = RetrievalLog.objects.filter(
        session_chunk_count__gt=0, textbook_chunk_count__gt=0
    ).count()

    active_logs = RetrievalLog.objects.filter(
        Q(session_chunk_count__gt=0) | Q(textbook_chunk_count__gt=0)
    )
    cross_source_rate = None
    if active_logs.exists():
        cross_source_rate = (cross_source_count / active_logs.count()) * 100

    has_retrieval_logs = total_retrieval_logs > 0

    similarity_buckets = {'bucket_0_02': 0, 'bucket_02_04': 0, 'bucket_04_06': 0, 'bucket_06_08': 0, 'bucket_08_10': 0, 'bucket_negative': 0, 'bucket_gt_1': 0}
    for val in RetrievalLog.objects.exclude(avg_similarity_top_k=None).values_list('avg_similarity_top_k', flat=True):
        if val is None:
            continue
        elif val < 0:
            similarity_buckets['bucket_negative'] += 1
        elif val > 1.0:
            similarity_buckets['bucket_gt_1'] += 1
        elif val < 0.2:
            similarity_buckets['bucket_0_02'] += 1
        elif val < 0.4:
            similarity_buckets['bucket_02_04'] += 1
        elif val < 0.6:
            similarity_buckets['bucket_04_06'] += 1
        elif val < 0.8:
            similarity_buckets['bucket_06_08'] += 1
        else:
            similarity_buckets['bucket_08_10'] += 1

    # ─── Model Generation Evaluation ──────────────────────────────────────────
    metric_stats = {}
    for gtype, label in GenerationMetric.GENERATION_TYPE_CHOICES:
        qs = GenerationMetric.objects.filter(generation_type=gtype)
        total = qs.count()
        if total == 0:
            continue
        stats = qs.aggregate(
            success_count=Count('id', filter=Q(success=True)),
            avg_duration=Avg('duration_ms'),
            cache_hit_count=Count('id', filter=Q(cache_hit=True)),
            validated_count=Count('id', filter=Q(output_validated=True)),
            avg_output_length=Avg('output_length'),
        )
        metric_stats[gtype] = {
            'total': total,
            'success_rate': (stats['success_count'] / total * 100),
            'avg_duration': stats['avg_duration'] or 0,
            'cache_hit_rate': (stats['cache_hit_count'] / total * 100),
            'validation_pass_rate': (stats['validated_count'] / total * 100),
            'avg_output_length': stats['avg_output_length'] or 0,
        }

    provider_success = {}
    for provider_code, provider_label in GenerationMetric.PROVIDER_CHOICES:
        qs = GenerationMetric.objects.filter(provider=provider_code)
        provider_success[provider_code] = {
            'label': provider_label,
            'total': qs.count(),
            'success_rate': (qs.filter(success=True).count() / qs.count() * 100) if qs.exists() else 0,
        }

    fallback_count = GenerationMetric.objects.filter(was_fallback=True).count()
    total_generation_attempts = GenerationMetric.objects.exclude(provider='cache').count()

    fallback_rate = None
    if total_generation_attempts > 0:
        fallback_rate = (fallback_count / total_generation_attempts) * 100

    quiz_qs = GenerationMetric.objects.filter(generation_type='quiz', output_length__gt=0)
    quiz_validation_pass_rate = (quiz_qs.filter(output_validated=True).count() / quiz_qs.count() * 100) if quiz_qs.exists() else None

    summary_qs = GenerationMetric.objects.filter(generation_type='summary', output_length__gt=0)
    summary_validation_pass_rate = (summary_qs.filter(output_validated=True).count() / summary_qs.count() * 100) if summary_qs.exists() else None

    context = {
        # RAG Retrieval
        'total_retrieval_logs': total_retrieval_logs,
        'avg_retrieval_latency': round(avg_retrieval_latency, 1) if avg_retrieval_latency is not None else None,
        'avg_top_k_similarity': round(avg_top_k_similarity, 3) if avg_top_k_similarity is not None else None,
        'cross_source_rate': round(cross_source_rate, 1) if cross_source_rate is not None else None,
        'similarity_buckets': similarity_buckets,
        'has_retrieval_logs': has_retrieval_logs,

        # Model Generation
        'metric_stats': metric_stats,
        'provider_success': provider_success,
        'fallback_rate': round(fallback_rate, 1) if fallback_rate is not None else None,
        'quiz_validation_pass_rate': round(quiz_validation_pass_rate, 1) if quiz_validation_pass_rate is not None else None,
        'summary_validation_pass_rate': round(summary_validation_pass_rate, 1) if summary_validation_pass_rate is not None else None,
        'has_generation_metrics': GenerationMetric.objects.exists(),
    }

    return render(request, 'quiz/evaluation.html', context)


def system_health(request):
    """
    System Health Dashboard.
    Operational metrics for pipeline health, provider performance, and error analysis.
    """
    # ─── System Operational Metrics ───────────────────────────────────────────
    total_textbook_chunks = TextbookChunk.objects.count()
    total_uploaded_chunks = UploadedChunk.objects.count()
    total_chunks = total_textbook_chunks + total_uploaded_chunks

    textbook_chunks_with_embeddings = TextbookChunk.objects.exclude(embedding=None).exclude(embedding=[]).exclude(embedding='').count()
    uploaded_chunks_with_embeddings = UploadedChunk.objects.exclude(embedding=None).exclude(embedding=[]).exclude(embedding='').count()

    embedding_coverage = 0
    if total_chunks > 0:
        embedding_coverage = ((textbook_chunks_with_embeddings + uploaded_chunks_with_embeddings) / total_chunks) * 100

    avg_chunks_per_session = 0
    session_count = UploadSession.objects.count()
    if session_count > 0:
        avg_chunks_per_session = total_uploaded_chunks / session_count

    topic_coverage = TextbookChunk.objects.exclude(topic=None).count()
    topic_coverage_pct = 0
    if total_textbook_chunks > 0:
        topic_coverage_pct = (topic_coverage / total_textbook_chunks) * 100

    quizzes_pending = Quiz.objects.filter(status__in=[Quiz.STATUS_PENDING, Quiz.STATUS_PROCESSING]).count()

    avg_processing_time = 0
    sessions_with_timing = UploadSession.objects.filter(
        processing_started_at__isnull=False,
        processing_completed_at__isnull=False,
    )
    if sessions_with_timing.exists():
        total_seconds = sum(
            (s.processing_completed_at - s.processing_started_at).total_seconds()
            for s in sessions_with_timing.iterator(chunk_size=100)
        )
        avg_processing_time = total_seconds / sessions_with_timing.count()

    # ─── Pipeline Operational Metrics ─────────────────────────────────────────
    total_quizzes = Quiz.objects.count()
    quizzes_completed = Quiz.objects.filter(status=Quiz.STATUS_COMPLETED).count()
    quizzes_failed = Quiz.objects.filter(status=Quiz.STATUS_FAILED).count()

    quiz_terminal = quizzes_completed + quizzes_failed
    quiz_success_rate = (quizzes_completed / quiz_terminal * 100) if quiz_terminal > 0 else 0

    total_sessions = UploadSession.objects.count()
    sessions_completed = UploadSession.objects.filter(processing_status=UploadSession.STATUS_COMPLETED).count()
    sessions_failed = UploadSession.objects.filter(processing_status=UploadSession.STATUS_FAILED).count()

    session_terminal = sessions_completed + sessions_failed
    summary_success_rate = (sessions_completed / session_terminal * 100) if session_terminal > 0 else 0

    total_attempts = QuizAttempt.objects.count()
    recs_completed = QuizAttempt.objects.filter(recommendation_status=QuizAttempt.RECOMMENDATION_COMPLETED).count()
    recs_failed = QuizAttempt.objects.filter(recommendation_status=QuizAttempt.RECOMMENDATION_FAILED).count()

    rec_terminal = recs_completed + recs_failed
    rec_success_rate = (recs_completed / rec_terminal * 100) if rec_terminal > 0 else 0

    overall_pipeline_success_rate = 0
    components = []
    if session_terminal > 0:
        components.append(summary_success_rate)
    if quiz_terminal > 0:
        components.append(quiz_success_rate)
    if rec_terminal > 0:
        components.append(rec_success_rate)
    if components:
        overall_pipeline_success_rate = sum(components) / len(components)

    # ─── Provider Operational Metrics ─────────────────────────────────────────
    provider_distribution = []
    if GenerationMetric.objects.exists():
        provider_data = (
            GenerationMetric.objects.exclude(provider='')
            .values('provider')
            .annotate(count=Count('id'))
            .order_by('-count')
        )
        for item in provider_data:
            provider_distribution.append({
                'provider': item['provider'].title(),
                'count': item['count'],
            })

    metric_stats = {}
    if GenerationMetric.objects.exists():
        for gtype, label in GenerationMetric.GENERATION_TYPE_CHOICES:
            qs = GenerationMetric.objects.filter(generation_type=gtype)
            metric_stats[gtype] = {
                'total': qs.count(),
                'success_rate': (qs.filter(success=True).count() / qs.count() * 100) if qs.exists() else 0,
                'avg_duration': qs.aggregate(avg=Avg('duration_ms'))['avg'] or 0,
                'cache_hit_rate': (qs.filter(cache_hit=True).count() / qs.count() * 100) if qs.exists() else 0,
            }

    # ─── Error Analysis ───────────────────────────────────────────────────────
    quiz_errors = (
        Quiz.objects.filter(status=Quiz.STATUS_FAILED, error_message__isnull=False)
        .exclude(error_message='')
        .values('error_message')
        .annotate(count=Count('id'))
        .order_by('-count')[:5]
    )

    session_errors = (
        UploadSession.objects.filter(processing_status=UploadSession.STATUS_FAILED, processing_error__isnull=False)
        .exclude(processing_error='')
        .values('processing_error')
        .annotate(count=Count('id'))
        .order_by('-count')[:5]
    )

    context = {
        # System Operational
        'total_textbook_chunks': total_textbook_chunks,
        'total_uploaded_chunks': total_uploaded_chunks,
        'total_chunks': total_chunks,
        'textbook_chunks_with_embeddings': textbook_chunks_with_embeddings,
        'uploaded_chunks_with_embeddings': uploaded_chunks_with_embeddings,
        'embedding_coverage': round(embedding_coverage, 1),
        'avg_chunks_per_session': round(avg_chunks_per_session, 1),
        'topic_coverage_pct': round(topic_coverage_pct, 1),
        'quizzes_pending': quizzes_pending,
        'avg_processing_time': round(avg_processing_time, 2),

        # Pipeline
        'total_quizzes': total_quizzes,
        'quizzes_completed': quizzes_completed,
        'quiz_success_rate': round(quiz_success_rate, 1),
        'total_sessions': total_sessions,
        'sessions_completed': sessions_completed,
        'summary_success_rate': round(summary_success_rate, 1),
        'total_attempts': total_attempts,
        'recs_completed': recs_completed,
        'rec_success_rate': round(rec_success_rate, 1),
        'overall_pipeline_success_rate': round(overall_pipeline_success_rate, 1),

        # Provider
        'provider_distribution': provider_distribution,
        'metric_stats': metric_stats,
        'has_generation_metrics': GenerationMetric.objects.exists(),

        # Errors
        'quiz_errors': list(quiz_errors),
        'session_errors': list(session_errors),
    }

    return render(request, 'quiz/system_health.html', context)


def user_analytics(request):
    """
    User Analytics Dashboard.
    User performance metrics and question quality proxies.
    """
    # ─── User Performance ─────────────────────────────────────────────────────
    avg_score = QuizAttempt.objects.aggregate(avg=Avg('score'))['avg'] or 0
    avg_total = QuizAttempt.objects.aggregate(avg=Avg('total_questions'))['avg'] or 10
    avg_score_pct = 0
    if avg_total > 0:
        avg_score_pct = (avg_score / avg_total) * 100

    total_attempts = QuizAttempt.objects.count()

    score_distribution = (
        QuizAttempt.objects.exclude(total_questions=0)
        .annotate(
            pct=Cast(F('score'), FloatField()) / Cast(F('total_questions'), FloatField()) * 100
        )
        .values('pct')
        .annotate(count=Count('id'))
        .order_by('pct')
    )

    score_buckets = {'0_20': 0, '21_40': 0, '41_60': 0, '61_80': 0, '81_100': 0}
    for item in score_distribution:
        pct = item['pct'] or 0
        if pct <= 20:
            score_buckets['0_20'] += item['count']
        elif pct <= 40:
            score_buckets['21_40'] += item['count']
        elif pct <= 60:
            score_buckets['41_60'] += item['count']
        elif pct <= 80:
            score_buckets['61_80'] += item['count']
        else:
            score_buckets['81_100'] += item['count']

    # ─── Question Quality Proxy ───────────────────────────────────────────────
    topic_accuracy = []
    topic_perf_data = (
        QuizAnswer.objects.filter(question__topic__isnull=False)
        .values('question__topic__title')
        .annotate(
            total=Count('id'),
            correct=Count('id', filter=Q(is_correct=True)),
        )
        .order_by('-total')[:10]
    )
    for item in topic_perf_data:
        total = item['total']
        correct = item['correct']
        accuracy = (correct / total * 100) if total > 0 else 0
        topic_accuracy.append({
            'topic': item['question__topic__title'],
            'total': total,
            'correct': correct,
            'accuracy': round(accuracy, 1),
        })

    topic_distribution = []
    topic_data = (
        Question.objects.exclude(topic=None)
        .values('topic__title')
        .annotate(count=Count('id'))
        .order_by('-count')[:10]
    )
    for item in topic_data:
        topic_distribution.append({
            'topic': item['topic__title'],
            'count': item['count'],
        })

    avg_questions_per_quiz = 0
    completed_quizzes = Quiz.objects.filter(status=Quiz.STATUS_COMPLETED).prefetch_related('questions')
    if completed_quizzes.exists():
        total_questions = sum(q.questions.count() for q in completed_quizzes)
        avg_questions_per_quiz = total_questions / completed_quizzes.count()

    context = {
        'avg_score': round(avg_score, 2),
        'avg_total': round(avg_total, 1),
        'avg_score_pct': round(avg_score_pct, 1),
        'total_attempts': total_attempts,
        'score_buckets': score_buckets,
        'topic_accuracy': topic_accuracy,
        'topic_distribution': topic_distribution,
        'avg_questions_per_quiz': round(avg_questions_per_quiz, 1),
    }

    return render(request, 'quiz/user_analytics.html', context)




