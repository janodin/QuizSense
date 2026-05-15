from django.shortcuts import render, redirect, get_object_or_404
from django.http import HttpResponseForbidden, JsonResponse
from django.contrib import messages
from django.utils import timezone
from django.db import transaction
from django.db.models import Avg, Count, Q, F, Sum, FloatField
from django.db.models.functions import Cast, Extract
from django.views.decorators.cache import cache_page
from django.views.decorators.vary import vary_on_cookie
from django.views.decorators.http import require_GET
from django.views.decorators.cache import never_cache
import time
from .models import Chapter, Topic, UploadSession, UploadedFile, Question, Quiz, QuizAttempt, QuizAnswer, TextbookChunk, UploadedChunk, GenerationMetric, RetrievalLog
from .forms import MultiFileUploadForm
from .services.pipeline_service import (
    queue_upload_session_processing,
    queue_quiz_generation,
)


_rate_limit_store = {}


def _rate_limit(key, interval=2):
    """Return True if the request is allowed, False if rate limited."""
    now = time.monotonic()
    last = _rate_limit_store.get(key)
    if last is not None and (now - last) < interval:
        return False
    _rate_limit_store[key] = now
    return True


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


def _sanitize_filename(name):
    """
    Sanitize an uploaded filename to prevent SuspiciousFileOperation
    caused by extremely long names or invalid characters.
    """
    import os
    from django.utils.text import get_valid_filename

    name = get_valid_filename(name)
    base, ext = os.path.splitext(name)
    # Truncate base to avoid path-length issues and collision loops.
    # 150 chars base + suffix + ext keeps total path well under 260
    # even after adding Django's uniqueness suffixes (_1, _2 ...).
    max_base_len = 150
    if len(base) > max_base_len:
        base = base[:max_base_len]
    # Fallback if the whole name was invalid chars
    if not base:
        base = "upload"
    return base + ext


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
                    # Sanitize filename to prevent SuspiciousFileOperation
                    # from extremely long names colliding in storage.
                    file_obj.name = _sanitize_filename(file_obj.name)
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
                logger.exception("Upload session processing failed: %s", e)
                try:
                    upload_session.delete()
                except Exception as del_exc:
                    logger.warning("Failed to clean up upload session %s: %s", upload_session.id, del_exc)
                error_msg = f"An error occurred during processing: {type(e).__name__}: {str(e)[:200]}"
                messages.error(request, error_msg)
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


@require_GET
@never_cache
def upload_session_status(request, upload_session_id):
    session_key = _get_session_key(request)
    if not _rate_limit(f"upload_session_status:{session_key}"):
        return JsonResponse({"status": "throttled"}, status=429)
    upload_session = get_object_or_404(UploadSession, id=upload_session_id)
    if not _check_ownership(request, upload_session):
        return HttpResponseForbidden("You do not have access to this upload session.")

    summary_text = upload_session.summary or ''
    return JsonResponse({
        'status': upload_session.processing_status,
        'error': upload_session.processing_error,
        'summary_ready': bool(summary_text.strip()),
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

    # Prevent duplicate quiz generation - check for existing active quizzes
    processing_quiz = Quiz.objects.filter(upload_session=upload_session, status=Quiz.STATUS_PROCESSING).first()
    if processing_quiz:
        return redirect('quiz_waiting', quiz_id=processing_quiz.id)

    existing_quiz = Quiz.objects.filter(upload_session=upload_session, status=Quiz.STATUS_COMPLETED).first()
    if existing_quiz:
        return redirect('take_quiz', quiz_id=existing_quiz.id)

    # Delete any failed quizzes for this session so a fresh one is created
    Quiz.objects.filter(upload_session=upload_session, status=Quiz.STATUS_FAILED).delete()

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


@require_GET
@never_cache
def quiz_status(request, quiz_id):
    """Return quiz generation status as JSON for polling."""
    session_key = _get_session_key(request)
    if not _rate_limit(f"quiz_status:{session_key}"):
        return JsonResponse({"status": "throttled"}, status=429)
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
    questions = list(quiz.questions.only('id', 'correct_answer').order_by('id'))

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

    with transaction.atomic():
        attempt = QuizAttempt.objects.create(
            quiz=quiz,
            session_key=request.session.session_key or '',
            total_questions=len(questions),
            recommendation_status=QuizAttempt.RECOMMENDATION_PENDING,
        )

        score = 0
        answers_to_create = []
        for question, selected in validated_answers:
            is_correct = selected == question.correct_answer
            if is_correct:
                score += 1
            answers_to_create.append(QuizAnswer(
                attempt=attempt,
                question=question,
                selected_answer=selected,
                is_correct=is_correct,
            ))

        QuizAnswer.objects.bulk_create(answers_to_create)
        attempt.score = score
        attempt.completed_at = timezone.now()
        attempt.save(update_fields=["score", "completed_at"])

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


@require_GET
@never_cache
def recommendation_status(request, attempt_id):
    """Return recommendation generation status as JSON for polling."""
    session_key = _get_session_key(request)
    if not _rate_limit(f"recommendation_status:{session_key}"):
        return JsonResponse({"status": "throttled"}, status=429)
    attempt = get_object_or_404(QuizAttempt, id=attempt_id)
    if not _check_ownership(request, attempt):
        return HttpResponseForbidden("You do not have access to this attempt.")

    return JsonResponse({
        'status': attempt.recommendation_status,
        'error': attempt.recommendation_error or None,
        'recommendation_html': attempt.ai_recommendation or None,
    })


@cache_page(300)
@vary_on_cookie
def evaluation(request):
    """
    Pure Evaluation Dashboard.
    Research-oriented metrics for RAG retrieval quality and model generation quality.
    Optimized to use database-level aggregation instead of Python loops.
    """
    # ─── RAG Retrieval Evaluation ─────────────────────────────────────────────
    retrieval_stats = RetrievalLog.objects.aggregate(
        total=Count('id'),
        avg_latency=Avg('retrieval_latency_ms'),
        cross_source=Count('id', filter=Q(session_chunk_count__gt=0, textbook_chunk_count__gt=0)),
        active=Count('id', filter=Q(Q(session_chunk_count__gt=0) | Q(textbook_chunk_count__gt=0))),
        sum_w=Sum(Cast(F('avg_similarity_top_k') * (F('session_chunk_count') + F('textbook_chunk_count')), output_field=FloatField()),
                  filter=Q(avg_similarity_top_k__isnull=False)),
        sum_k=Sum(F('session_chunk_count') + F('textbook_chunk_count'), filter=Q(avg_similarity_top_k__isnull=False)),
        # Similarity buckets
        bucket_negative=Count('id', filter=Q(avg_similarity_top_k__lt=0)),
        bucket_0_02=Count('id', filter=Q(avg_similarity_top_k__gte=0, avg_similarity_top_k__lt=0.2)),
        bucket_02_04=Count('id', filter=Q(avg_similarity_top_k__gte=0.2, avg_similarity_top_k__lt=0.4)),
        bucket_04_06=Count('id', filter=Q(avg_similarity_top_k__gte=0.4, avg_similarity_top_k__lt=0.6)),
        bucket_06_08=Count('id', filter=Q(avg_similarity_top_k__gte=0.6, avg_similarity_top_k__lt=0.8)),
        bucket_08_10=Count('id', filter=Q(avg_similarity_top_k__gte=0.8, avg_similarity_top_k__lte=1.0)),
        bucket_gt_1=Count('id', filter=Q(avg_similarity_top_k__gt=1.0)),
    )

    total_retrieval_logs = retrieval_stats['total']
    avg_retrieval_latency = retrieval_stats['avg_latency']
    
    avg_top_k_similarity = None
    if retrieval_stats['sum_k']:
        avg_top_k_similarity = retrieval_stats['sum_w'] / retrieval_stats['sum_k']

    cross_source_rate = None
    if retrieval_stats['active']:
        cross_source_rate = (retrieval_stats['cross_source'] / retrieval_stats['active']) * 100

    has_retrieval_logs = total_retrieval_logs > 0

    similarity_buckets = {
        'bucket_negative': retrieval_stats['bucket_negative'],
        'bucket_0_02': retrieval_stats['bucket_0_02'],
        'bucket_02_04': retrieval_stats['bucket_02_04'],
        'bucket_04_06': retrieval_stats['bucket_04_06'],
        'bucket_06_08': retrieval_stats['bucket_06_08'],
        'bucket_08_10': retrieval_stats['bucket_08_10'],
        'bucket_gt_1': retrieval_stats['bucket_gt_1'],
    }

    # ─── Model Generation Evaluation ──────────────────────────────────────────
    # Single grouped query for generation type stats
    type_stats_qs = GenerationMetric.objects.values('generation_type').annotate(
        total=Count('id'),
        success_count=Count('id', filter=Q(success=True)),
        avg_duration=Avg('duration_ms'),
        cache_hit_count=Count('id', filter=Q(cache_hit=True)),
        validated_count=Count('id', filter=Q(output_validated=True)),
        avg_output_length=Avg('output_length'),
    )
    
    metric_stats = {}
    for row in type_stats_qs:
        gtype = row['generation_type']
        total = row['total']
        metric_stats[gtype] = {
            'total': total,
            'success_rate': (row['success_count'] / total * 100) if total else 0,
            'avg_duration': row['avg_duration'] or 0,
            'cache_hit_rate': (row['cache_hit_count'] / total * 100) if total else 0,
            'validation_pass_rate': (row['validated_count'] / total * 100) if total else 0,
            'avg_output_length': row['avg_output_length'] or 0,
        }

    # Quiz and Summary validation in single aggregates
    quiz_stats = GenerationMetric.objects.filter(generation_type='quiz', output_length__gt=0).aggregate(
        total=Count('id'),
        validated=Count('id', filter=Q(output_validated=True)),
    )
    quiz_validation_pass_rate = (quiz_stats['validated'] / quiz_stats['total'] * 100) if quiz_stats['total'] else None

    summary_stats = GenerationMetric.objects.filter(generation_type='summary', output_length__gt=0).aggregate(
        total=Count('id'),
        validated=Count('id', filter=Q(output_validated=True)),
    )
    summary_validation_pass_rate = (summary_stats['validated'] / summary_stats['total'] * 100) if summary_stats['total'] else None

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

        'quiz_validation_pass_rate': round(quiz_validation_pass_rate, 1) if quiz_validation_pass_rate is not None else None,
        'summary_validation_pass_rate': round(summary_validation_pass_rate, 1) if summary_validation_pass_rate is not None else None,
        'has_generation_metrics': GenerationMetric.objects.exists(),
    }

    return render(request, 'quiz/evaluation.html', context)


@cache_page(300)
@vary_on_cookie
def system_health(request):
    """
    System Health Dashboard.
    Operational metrics for pipeline health, provider performance, and error analysis.
    Optimized to use database-level aggregation instead of Python loops.
    """
    # ─── System Operational Metrics ───────────────────────────────────────────
    chunk_stats = TextbookChunk.objects.aggregate(
        total=Count('id'),
        with_embeddings=Count('id', filter=~Q(embedding=None) & ~Q(embedding=[]) & ~Q(embedding='')),
        with_topic=Count('id', filter=Q(topic__isnull=False)),
    )
    total_textbook_chunks = chunk_stats['total']
    textbook_chunks_with_embeddings = chunk_stats['with_embeddings']
    topic_coverage = chunk_stats['with_topic']

    uploaded_stats = UploadedChunk.objects.aggregate(
        total=Count('id'),
        with_embeddings=Count('id', filter=~Q(embedding=None) & ~Q(embedding=[]) & ~Q(embedding='')),
    )
    total_uploaded_chunks = uploaded_stats['total']
    uploaded_chunks_with_embeddings = uploaded_stats['with_embeddings']

    total_chunks = total_textbook_chunks + total_uploaded_chunks

    embedding_coverage = 0
    if total_chunks > 0:
        embedding_coverage = ((textbook_chunks_with_embeddings + uploaded_chunks_with_embeddings) / total_chunks) * 100

    session_count = UploadSession.objects.count()
    avg_chunks_per_session = (total_uploaded_chunks / session_count) if session_count else 0

    topic_coverage_pct = (topic_coverage / total_textbook_chunks * 100) if total_textbook_chunks else 0

    quizzes_pending = Quiz.objects.filter(status__in=[Quiz.STATUS_PENDING, Quiz.STATUS_PROCESSING]).count()

    avg_processing_time_result = UploadSession.objects.filter(
        processing_started_at__isnull=False,
        processing_completed_at__isnull=False,
    ).annotate(
        duration_seconds=Extract(F('processing_completed_at'), 'epoch') - Extract(F('processing_started_at'), 'epoch')
    ).aggregate(avg=Avg('duration_seconds'))
    avg_processing_time = avg_processing_time_result['avg'] or 0

    # ─── Pipeline Operational Metrics ─────────────────────────────────────────
    quiz_stats = Quiz.objects.aggregate(
        total=Count('id'),
        completed=Count('id', filter=Q(status=Quiz.STATUS_COMPLETED)),
        failed=Count('id', filter=Q(status=Quiz.STATUS_FAILED)),
    )
    total_quizzes = quiz_stats['total']
    quizzes_completed = quiz_stats['completed']
    quizzes_failed = quiz_stats['failed']

    quiz_terminal = quizzes_completed + quizzes_failed
    quiz_success_rate = (quizzes_completed / quiz_terminal * 100) if quiz_terminal > 0 else 0

    session_stats = UploadSession.objects.aggregate(
        total=Count('id'),
        completed=Count('id', filter=Q(processing_status=UploadSession.STATUS_COMPLETED)),
        failed=Count('id', filter=Q(processing_status=UploadSession.STATUS_FAILED)),
    )
    total_sessions = session_stats['total']
    sessions_completed = session_stats['completed']
    sessions_failed = session_stats['failed']

    session_terminal = sessions_completed + sessions_failed
    summary_success_rate = (sessions_completed / session_terminal * 100) if session_terminal > 0 else 0

    attempt_stats = QuizAttempt.objects.aggregate(
        total=Count('id'),
        recs_completed=Count('id', filter=Q(recommendation_status=QuizAttempt.RECOMMENDATION_COMPLETED)),
        recs_failed=Count('id', filter=Q(recommendation_status=QuizAttempt.RECOMMENDATION_FAILED)),
    )
    total_attempts = attempt_stats['total']
    recs_completed = attempt_stats['recs_completed']
    recs_failed = attempt_stats['recs_failed']

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
    has_generation_metrics = GenerationMetric.objects.exists()

    provider_distribution = []
    if has_generation_metrics:
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
    if has_generation_metrics:
        type_stats_qs = GenerationMetric.objects.values('generation_type').annotate(
            total=Count('id'),
            success_count=Count('id', filter=Q(success=True)),
            avg_duration=Avg('duration_ms'),
            cache_hit_count=Count('id', filter=Q(cache_hit=True)),
        )
        for row in type_stats_qs:
            gtype = row['generation_type']
            total = row['total']
            metric_stats[gtype] = {
                'total': total,
                'success_rate': (row['success_count'] / total * 100) if total else 0,
                'avg_duration': row['avg_duration'] or 0,
                'cache_hit_rate': (row['cache_hit_count'] / total * 100) if total else 0,
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
        'has_generation_metrics': has_generation_metrics,

        # Errors
        'quiz_errors': list(quiz_errors),
        'session_errors': list(session_errors),
    }

    return render(request, 'quiz/system_health.html', context)


@cache_page(300)
@vary_on_cookie
def user_analytics(request):
    """
    User Analytics Dashboard.
    User performance metrics and question quality proxies.
    Optimized to use database-level aggregation instead of Python loops.
    """
    # ─── User Performance ─────────────────────────────────────────────────────
    attempt_avgs = QuizAttempt.objects.aggregate(
        avg_score=Avg('score'),
        avg_total=Avg('total_questions'),
        total=Count('id'),
    )
    avg_score = attempt_avgs['avg_score'] or 0
    avg_total = attempt_avgs['avg_total'] or 10
    avg_score_pct = (avg_score / avg_total * 100) if avg_total > 0 else 0
    total_attempts = attempt_avgs['total']

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

    quiz_question_stats = Quiz.objects.filter(
        status=Quiz.STATUS_COMPLETED
    ).aggregate(
        total_quizzes=Count('id', distinct=True),
        total_questions=Count('questions'),
    )
    total_completed = quiz_question_stats['total_quizzes']
    total_questions_count = quiz_question_stats['total_questions']
    avg_questions_per_quiz = (total_questions_count / total_completed) if total_completed else 0

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




