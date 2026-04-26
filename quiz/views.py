from django.shortcuts import render, redirect, get_object_or_404
from django.http import HttpResponseForbidden
from django.contrib import messages
from django.utils import timezone
from .models import Chapter, Topic, UploadSession, UploadedFile, Question, Quiz, QuizAttempt, QuizAnswer
from .forms import MultiFileUploadForm
from .services.file_processor import extract_text_from_pdf, extract_text_from_docx
from .services.minimax_service import generate_mcq_questions, generate_summary
from .services.rag_service import ingest_uploaded_file_chunks, retrieve_context_for_session


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


def _normalize_topic(title):
    """Normalize a topic title for fuzzy matching — lowercase, strip punctuation."""
    import re
    return re.sub(r'[^a-z0-9\s]', '', title.lower()).strip()


def _find_topic_for_chapter(chapter, ai_topic_name):
    """
    Map an AI-generated topic name to an existing seeded Topic for this chapter.
    Falls back to creating a new topic only when no reasonable match exists.
    """
    if not ai_topic_name:
        return None

    normalized_ai = _normalize_topic(ai_topic_name)

    # 1. Exact match (case-insensitive)
    existing = list(Topic.objects.filter(chapter=chapter))
    for t in existing:
        if _normalize_topic(t.title) == normalized_ai:
            return t

    # 2. Substring match — AI topic contains existing title or vice versa
    for t in existing:
        norm_title = _normalize_topic(t.title)
        if norm_title and (norm_title in normalized_ai or normalized_ai in norm_title):
            return t

    # 3. No match found — create a new topic (last resort)
    return Topic.objects.create(chapter=chapter, title=ai_topic_name)


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
                successful_files = []
                for file_obj in files:
                    filename = file_obj.name.lower()
                    if filename.endswith('.pdf'):
                        file_type = 'pdf'
                        extracted_text = extract_text_from_pdf(file_obj)
                    elif filename.endswith('.docx'):
                        file_type = 'docx'
                        extracted_text = extract_text_from_docx(file_obj)
                    else:
                        continue

                    if not extracted_text or not extracted_text.strip():
                        continue

                    uploaded_file = UploadedFile.objects.create(
                        upload_session=upload_session,
                        chapter=chapter,
                        file=file_obj,
                        file_type=file_type,
                        extracted_text=extracted_text,
                    )
                    ingest_uploaded_file_chunks(uploaded_file)
                    successful_files.append(uploaded_file)

                if not successful_files:
                    upload_session.delete()
                    messages.error(request, "Could not extract text from the uploaded files. Please try different files.")
                    return render(request, 'quiz/home.html', {'form': form})

                context_bundle = retrieve_context_for_session(upload_session, mode='summary')
                upload_session.summary = generate_summary(
                    context_bundle['context_text'],
                    chapter.title if chapter else 'Fundamentals of Programming',
                    cross_reference_notes=context_bundle['cross_reference_notes'],
                )
                upload_session.save(update_fields=['summary'])

            except Exception as e:
                import logging
                logger = logging.getLogger(__name__)
                logger.exception("Upload session processing failed")
                upload_session.delete()
                messages.error(request, f"Error during processing: {e}")
                return render(request, 'quiz/home.html', {'form': form})

            return redirect('study_summary', upload_session_id=upload_session.id)

    return render(request, 'quiz/home.html', {'form': form})


def study_summary(request, upload_session_id):
    upload_session = get_object_or_404(UploadSession, id=upload_session_id)
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


def generate_quiz(request, upload_session_id):
    upload_session = get_object_or_404(UploadSession, id=upload_session_id)
    if not _check_ownership(request, upload_session):
        return HttpResponseForbidden("You do not have access to this upload session.")
    chapter = upload_session.chapter
    if not chapter:
        messages.error(request, "No chapter was associated with this upload session.")
        return redirect('home')

    if not upload_session.files.filter(extracted_text__gt='').exists():
        messages.error(request, "No text could be extracted from the uploaded files.")
        return redirect('home')

    context_bundle = retrieve_context_for_session(upload_session, mode='quiz')
    if not context_bundle['context_text'].strip():
        messages.error(request, "Could not build retrieval context for quiz generation.")
        return redirect('home')

    try:
        mcq_list = generate_mcq_questions(
            context_bundle['context_text'],
            chapter.title,
            cross_reference_notes=context_bundle['cross_reference_notes'],
        )
    except ValueError as exc:
        return render(request, 'quiz/generating.html', {
            'upload_session': upload_session,
            'error': True,
            'error_message': str(exc),
        })

    primary_file = upload_session.files.order_by('id').first()
    quiz = Quiz.objects.create(
        chapter=chapter,
        upload_session=upload_session,
        uploaded_file=primary_file,
    )

    for mcq in mcq_list:
        topic = _find_topic_for_chapter(chapter, mcq.get('topic', ''))
        question = Question.objects.create(
            chapter=chapter,
            topic=topic,
            uploaded_file=primary_file,
            text=mcq['question'],
            choice_a=mcq['choices']['A'],
            choice_b=mcq['choices']['B'],
            choice_c=mcq['choices']['C'],
            choice_d=mcq['choices']['D'],
            correct_answer=mcq['correct_answer'],
        )
        quiz.questions.add(question)

    return redirect('take_quiz', quiz_id=quiz.id)


def take_quiz(request, quiz_id):
    quiz = get_object_or_404(Quiz, id=quiz_id)
    if quiz.upload_session and not _check_ownership(request, quiz.upload_session):
        return HttpResponseForbidden("You do not have access to this quiz.")
    questions = list(quiz.questions.all())
    return render(request, 'quiz/take_quiz.html', {'quiz': quiz, 'questions': questions})

def submit_quiz(request, quiz_id):
    if request.method != 'POST':
        return redirect('take_quiz', quiz_id=quiz_id)

    quiz = get_object_or_404(Quiz, id=quiz_id)
    if quiz.upload_session and not _check_ownership(request, quiz.upload_session):
        return HttpResponseForbidden("You do not have access to this quiz.")
    questions = list(quiz.questions.all())

    if not request.session.session_key:
        request.session.create()

    valid_choices = {'A', 'B', 'C', 'D'}

    # Collect and validate all answers before creating the attempt
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

    # All answers present — create the attempt
    attempt = QuizAttempt.objects.create(
        quiz=quiz,
        session_key=request.session.session_key or '',
        total_questions=len(questions),
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
            'question_text': question.text,
            'correct_answer': question.correct_answer,
            'selected_answer': selected,
            'is_correct': is_correct,
            'topic': question.topic.title if question.topic else 'General',
        })

    attempt.score = score
    attempt.completed_at = timezone.now()

    # Generate AI recommendations
    try:
        from .services.minimax_service import generate_recommendations
        recommendation = generate_recommendations(attempt, questions_with_answers)
        attempt.ai_recommendation = recommendation
        attempt.save()
    except Exception as exc:
        import logging
        logger = logging.getLogger(__name__)
        logger.warning(f"AI recommendation generation failed for attempt {attempt.id}: {exc}")
        messages.warning(
            request,
            "Quiz submitted successfully, but study recommendations could not be generated."
        )
        attempt.save()
        return redirect('quiz_results', attempt_id=attempt.id)

    return redirect('quiz_results', attempt_id=attempt.id)


def quiz_results(request, attempt_id):
    attempt = get_object_or_404(QuizAttempt, id=attempt_id)
    if not _check_ownership(request, attempt):
        return HttpResponseForbidden("You do not have access to these results.")
    incorrect = attempt.total_questions - attempt.score
    
    # Calculate topic-wise performance
    answers = attempt.answers.select_related('question', 'question__topic').all()
    topic_stats = {}
    
    for answer in answers:
        topic_name = answer.question.topic.title if answer.question.topic else 'General'
        if topic_name not in topic_stats:
            topic_stats[topic_name] = {'correct': 0, 'total': 0}
        topic_stats[topic_name]['total'] += 1
        if answer.is_correct:
            topic_stats[topic_name]['correct'] += 1
    
    # Calculate percentages
    for topic, stats in topic_stats.items():
        stats['percentage'] = round((stats['correct'] / stats['total']) * 100) if stats['total'] > 0 else 0
    
    # Sort by percentage (weakest first)
    topic_performance = sorted(topic_stats.items(), key=lambda x: x[1]['percentage'])
    
    context = {
        'attempt': attempt,
        'incorrect': incorrect,
        'topic_performance': topic_performance,
    }
    return render(request, 'quiz/results.html', context)


def review_quiz(request, attempt_id):
    attempt = get_object_or_404(QuizAttempt, id=attempt_id)
    if not _check_ownership(request, attempt):
        return HttpResponseForbidden("You do not have access to this review.")
    answers = attempt.answers.select_related('question').order_by('id')
    return render(request, 'quiz/review.html', {'attempt': attempt, 'answers': answers})


def quiz_insights(request, attempt_id):
    """Display detailed insights and recommendations for a quiz attempt."""
    attempt = get_object_or_404(QuizAttempt, id=attempt_id)
    if not _check_ownership(request, attempt):
        return HttpResponseForbidden("You do not have access to these insights.")
    answers = attempt.answers.select_related('question', 'question__topic', 'question__chapter').all()
    
    # Topic-wise performance analysis
    topic_stats = {}
    for answer in answers:
        topic_name = answer.question.topic.title if answer.question.topic else 'General'
        if topic_name not in topic_stats:
            topic_stats[topic_name] = {
                'correct': 0,
                'total': 0,
                'questions': []
            }
        topic_stats[topic_name]['total'] += 1
        if answer.is_correct:
            topic_stats[topic_name]['correct'] += 1
        topic_stats[topic_name]['questions'].append({
            'text': answer.question.text,
            'is_correct': answer.is_correct,
            'selected': answer.selected_answer,
            'correct': answer.question.correct_answer,
        })
    
    # Calculate percentages and identify weak topics
    weak_topics = []
    strong_topics = []
    
    for topic, stats in topic_stats.items():
        stats['percentage'] = round((stats['correct'] / stats['total']) * 100) if stats['total'] > 0 else 0
        if stats['percentage'] < 70:
            weak_topics.append((topic, stats))
        elif stats['percentage'] >= 80:
            strong_topics.append((topic, stats))
    
    # Sort by performance
    weak_topics.sort(key=lambda x: x[1]['percentage'])
    strong_topics.sort(key=lambda x: x[1]['percentage'], reverse=True)
    
    # Chapter-wise breakdown
    chapter_name = attempt.quiz.chapter.title if attempt.quiz.chapter else 'General'
    
    context = {
        'attempt': attempt,
        'chapter_name': chapter_name,
        'topic_stats': sorted(topic_stats.items(), key=lambda x: x[1]['percentage']),
        'weak_topics': weak_topics,
        'strong_topics': strong_topics,
        'total_topics': len(topic_stats),
    }
    
    return render(request, 'quiz/insights.html', context)


