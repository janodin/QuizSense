"""
Simplified Pipeline Service for QuizSense.

Provides unified generation pipeline for:
- Summary Generation (with RAG)
- Quiz Generation
- Recommendations Generation

Key functions:
- process_upload_session_simple(): handles file extraction, chunking, embedding, summary
- _process_quiz_for_session(): generates quiz from upload session
- _process_recommendations_for_attempt(): generates study recommendations

All use the configured AI provider (default: openai/gpt-oss-120b via DeepInfra API).
"""

import hashlib
import json
import logging
import time
import threading
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

from django.core.cache import cache
from django.db import close_old_connections, transaction
from django.utils import timezone

from ..models import (
    Chapter,
    GenerationMetric,
    Question,
    Quiz,
    QuizAttempt,
    TextbookChunk,
    Topic,
    UploadSession,
    UploadedChunk,
)
from .chunking_service import split_text_into_chunks
from .embedding_service import embed_texts_batched
from .rag_service import retrieve_context_for_session

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    "You are an expert programming instructor creating educational content. "
    "Follow all formatting instructions precisely. Return only the requested content."
)


class GenerationType(Enum):
    SUMMARY = "summary"
    QUIZ = "quiz"
    RECOMMENDATIONS = "recommendations"


@dataclass
class GenerationResult:
    success: bool
    data: Any = None
    error: Optional[str] = None
    generation_type: Optional[GenerationType] = None
    duration_ms: float = 0
    provider_name: Optional[str] = None


@dataclass
class GenerationContext:
    context_text: str = ""
    cross_reference_notes: str = ""
    session_chunks: list = field(default_factory=list)
    textbook_chunks: list = field(default_factory=list)


class AIProvider(ABC):
    @abstractmethod
    def generate_summary(self, text: str, chapter_title: str, cross_reference_notes: str) -> str:
        pass

    @abstractmethod
    def generate_mcq(self, text: str, chapter_title: str, cross_reference_notes: str) -> list:
        pass

    @abstractmethod
    def generate_recommendations(
        self, quiz_attempt: QuizAttempt, questions_with_answers: list
    ) -> str:
        pass

    @abstractmethod
    def extract_concepts(self, text: str) -> str:
        """Map-phase: extract key concepts from a chunk of text."""
        pass

    @abstractmethod
    def get_provider_name(self) -> str:
        pass


class ModelProvider(AIProvider):
    """Uses the configured AI provider API (default: DeepInfra with openai/gpt-oss-120b)."""
    def __init__(self):
        self._url = "https://api.deepinfra.com/v1/openai/chat/completions"
        self._model = "openai/gpt-oss-120b"

    def _make_request(self, prompt: str, max_tokens: int = 8192, timeout: int = 120) -> str:
        import json
        import time
        import requests
        from django.conf import settings

        start_time = time.time()
        logger.info("[AI_PROVIDER] Sending request (prompt_length=%d, max_tokens=%d, timeout=%d)", len(prompt), max_tokens, timeout)

        headers = {
            "Authorization": f"Bearer {settings.AI_PROVIDER_API_KEY}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            "max_tokens": max_tokens,
            "temperature": 0.7,
        }

        try:
            response = requests.post(self._url, headers=headers, json=payload, timeout=timeout)
            elapsed = time.time() - start_time
            logger.info("[AI_PROVIDER] Response received in %.1fs (status=%d)", elapsed, response.status_code)
        except requests.exceptions.Timeout:
            logger.error("[AI_PROVIDER] Request timed out after %ds", timeout)
            raise ValueError(f"AI provider request timed out after {timeout} seconds")
        except requests.exceptions.ConnectionError as e:
            logger.error("[AI_PROVIDER] Connection error: %s", e)
            raise ValueError(f"AI provider connection error: {e}")

        if response.status_code != 200:
            try:
                error_data = response.json()
                msg = error_data.get("error", {}).get("message") or response.text
            except Exception:
                msg = response.text
            logger.error("[AI_PROVIDER] API error (status=%d): %s", response.status_code, msg)
            raise ValueError(f"AI provider error ({response.status_code}): {msg}")

        try:
            data = response.json()
            content = data["choices"][0]["message"]["content"]
            if not content:
                raise ValueError("AI provider returned empty response")
            return content
        except (KeyError, IndexError, json.JSONDecodeError) as e:
            logger.error("[AI_PROVIDER] Failed to parse response: %s", e)
            raise ValueError(f"AI provider response parsing failed: {e}")

    def generate_summary(
        self, text: str, chapter_title: str, cross_reference_notes: str
    ) -> str:
        prompt = (
            "You are an expert programming instructor. Based on the context below, "
            "write a polished, richly-formatted study summary.\n\n"
            f"Chapter: {chapter_title}\n\n"
            f"Context:\n{text[:12000]}\n\n"
            f"Cross-Reference Notes:\n{cross_reference_notes or 'N/A'}\n\n"
            "Formatting rules (IMPORTANT):\n"
            "- Wrap every programming keyword, function name, variable, syntax, or code snippet in backticks, e.g. `for`, `while`, `print()`, `x = 5`, `if-else`.\n"
            "- Use **bold** for emphasis on critical terms and section labels.\n"
            "- Use a clean Markdown structure with headings and bullet lists.\n\n"
            "Use this exact Markdown structure:\n"
            "## Study Summary\n"
            "### Overview\n"
            "Write 2-3 clear sentences explaining the main idea of the material. Use backticks for any code mentioned.\n\n"
            "### Key Concepts\n"
            "- **Concept name**: Explanation with `code` keywords highlighted.\n"
            "- **Another concept**: Explanation with `code` keywords highlighted.\n\n"
            "### Review Focus\n"
            "- **Label**: Actionable item with `code` keywords where relevant.\n"
            "- **Label**: Another actionable item with `code` keywords.\n\n"
            "Return only the study summary Markdown now:"
        )
        return self._make_request(prompt)

    def extract_concepts(self, text: str) -> str:
        prompt = (
            "You are analyzing a section of a programming textbook. "
            "Read the text carefully and extract the key information.\n\n"
            f"Text:\n{text[:12000]}\n\n"
            "Extract and list:\n"
            "1. Key concepts and definitions mentioned\n"
            "2. Important code patterns, algorithms, or techniques\n"
            "3. Best practices, rules, warnings, or common mistakes\n"
            "4. Topics covered in this section\n\n"
            "Be thorough but concise. Return as plain text."
        )
        return self._make_request(prompt)

    def generate_mcq(
        self, text: str, chapter_title: str, cross_reference_notes: str
    ) -> list:
        import json
        import re

        def _extract_json_array(raw_text: str) -> str:
            cleaned_text = re.sub(r'<think>.*?</think>', '', raw_text, flags=re.DOTALL)
            cleaned_text = re.sub(r'<reasoning>.*?</reasoning>', '', cleaned_text, flags=re.DOTALL)
            cleaned_text = re.sub(r'```(?:json)?\s*', '', cleaned_text).strip().strip('`')
            start = cleaned_text.find("[")
            end = cleaned_text.rfind("]")
            if start == -1 or end == -1:
                raise ValueError(f"AI Response parsing failed. Raw starts with: {raw_text[:100]}")
            return cleaned_text[start:end + 1]

        def _parse_questions(raw_text: str) -> list:
            return json.loads(_extract_json_array(raw_text))

        prompt = (
            "You are an expert programming instructor. Generate exactly 10 MCQs as a JSON array.\n\n"
            f"Chapter: {chapter_title}\n\n"
            f"Retrieved Context:\n{text[:6000]}\n\n"
            f"Cross-Reference Notes (textbook topic matches):\n{cross_reference_notes or 'N/A'}\n\n"
            'Return ONLY a valid JSON array. Each object: {"question", "choices":{"A","B","C","D"}, "correct_answer", "topic"}\n'
            'Escape quotes inside strings. Do not use markdown. Do not add comments.\n\n'
            'IMPORTANT: Do NOT include any reasoning, thinking, or explanation. Output ONLY the JSON array.'
        )
        raw = self._make_request(prompt, max_tokens=4096)

        try:
            questions = _parse_questions(raw)
        except json.JSONDecodeError as exc:
            logger.warning(
                "[AI_PROVIDER] Quiz JSON parse failed; attempting repair: %s",
                exc,
            )
            repair_prompt = (
                "Convert the malformed quiz JSON below into a valid JSON array.\n"
                "Preserve the same questions, choices, correct_answer letters, and topics.\n"
                "Return ONLY valid JSON. No markdown, no explanation.\n\n"
                f"Malformed JSON:\n{_extract_json_array(raw)}"
            )
            repaired = self._make_request(repair_prompt, max_tokens=4096, timeout=90)
            questions = _parse_questions(repaired)
        return questions[:10]

    def generate_recommendations(
        self, quiz_attempt: QuizAttempt, questions_with_answers: list
    ) -> str:
        chapter_title = (
            quiz_attempt.quiz.chapter.title if quiz_attempt.quiz.chapter else "Fundamentals"
        )

        topic_lines = []
        wrong_lines = []
        for qa in questions_with_answers:
            topic_lines.append(f"  - [{qa['topic']}] {qa['question'][:120]}...")
            if not qa['is_correct']:
                wrong_lines.append(
                    f"  - Topic: {qa['topic']} | Q: {qa['question'][:100]}... "
                    f"| Correct: {qa['correct_answer']} | Student answered: {qa['selected_answer']}"
                )

        topic_summary = "\n".join(topic_lines) or "  (no data)"
        wrong_summary = "\n".join(wrong_lines) or "  (all correct!)"

        prompt = (
            f"You are an expert programming instructor. Provide concise, actionable study recommendations "
            f"for a student who scored {quiz_attempt.score}/{quiz_attempt.total_questions} on \"{chapter_title}\".\n\n"
            f"--- All Questions ---\n{topic_summary}\n\n"
            f"--- Incorrect Answers ---\n{wrong_summary}\n\n"
            f"Focus on the weak topics and explain what the student should study to improve."
        )
        return self._make_request(prompt, timeout=90)

    def get_provider_name(self) -> str:
        return "deepinfra"


_default_provider: Optional[ModelProvider] = None
_provider_lock = threading.Lock()


def get_generation_provider() -> ModelProvider:
    global _default_provider
    if _default_provider is None:
        with _provider_lock:
            if _default_provider is None:
                _default_provider = ModelProvider()
    return _default_provider


# ─── Cache helpers for AI-generated outputs ──────────────────────────────────


def _get_content_hash(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()[:16]


def _cache_key(prefix: str, chapter_id: Any, content_hash: str) -> str:
    return f"quizsense:{prefix}:ch{chapter_id}:{content_hash}"


def _validate_summary(data: Any) -> bool:
    if not isinstance(data, str):
        return False
    has_headers = (
        "## Study Summary" in data
        and "### Overview" in data
        and "### Key Concepts" in data
        and "### Review Focus" in data
    )
    # Check for meaningful content (not just headers)
    has_content = len(data) > 200
    # Check for bullet points or numbered lists (actual content)
    has_lists = any(marker in data for marker in ['- ', '* ', '1. ', '• '])
    return has_headers and has_content and has_lists


def _process_summary_for_session(upload_session: UploadSession, timer=None) -> GenerationResult:
    chapter_title = (
        upload_session.chapter.title if upload_session.chapter else "Fundamentals of Programming"
    )

    def _detail(msg):
        if timer:
            timer.detail(msg)
        logger.info("[SESSION %s] %s", upload_session.id, msg)

    all_text = "\n\n".join(
        f.extracted_text.strip()
        for f in upload_session.files.all()
        if f.extracted_text and f.extracted_text.strip()
    )

    cache_hit = False
    result = None

    if not all_text.strip():
        result = GenerationResult(
            success=False,
            error="No extracted text was available for summary generation.",
            generation_type=GenerationType.SUMMARY,
        )
    else:
        chapter_id = upload_session.chapter_id or 0
        cache_key = _cache_key("summary", chapter_id, _get_content_hash(all_text))

        cached_summary = cache.get(cache_key)
        if cached_summary:
            cache_hit = True
            _detail("Summary cache hit — returning cached result.")
            upload_session.summary = cached_summary
            close_old_connections()
            upload_session.save(update_fields=["summary"])
            result = GenerationResult(
                success=True,
                data=cached_summary,
                generation_type=GenerationType.SUMMARY,
                provider_name='cache',
            )
        else:
            provider = get_generation_provider()
            total_len = len(all_text)

            # Always run RAG to retrieve textbook context
            _detail("Cache miss — retrieving context via RAG...")
            rag_result = retrieve_context_for_session(upload_session, mode='summary')
            combined_text = rag_result['context_text'] or all_text[:12000]
            cross_ref = rag_result['cross_reference_notes']
            _detail(f"RAG context ready: {len(combined_text)} chars")

            if total_len > 15000:
                _detail(f"Long document ({total_len} chars) — using map-reduce summary with RAG context...")
                result = _map_reduce_summary(
                    upload_session, all_text, chapter_title, provider, _detail,
                    rag_context=combined_text, cross_ref=cross_ref,
                )
            else:
                _detail(f"Document is short ({total_len} chars) — using single-pass summary...")
                _detail("Calling AI provider...")
                try:
                    summary_text = provider.generate_summary(combined_text, chapter_title, cross_ref)
                    result = GenerationResult(
                        success=True,
                        data=summary_text,
                        generation_type=GenerationType.SUMMARY,
                        provider_name=provider.get_provider_name(),
                    )
                except Exception as e:
                    _detail(f"AI call failed: {e}")
                    result = GenerationResult(
                        success=False,
                        error=str(e),
                        generation_type=GenerationType.SUMMARY,
                        provider_name=provider.get_provider_name(),
                    )

            if result and result.success:
                if _validate_summary(result.data):
                    _detail(f"AI response received: {len(result.data)} chars")
                    upload_session.summary = result.data
                    close_old_connections()
                    upload_session.save(update_fields=["summary"])
                    cache.set(cache_key, result.data, timeout=60 * 60 * 24 * 7)
                else:
                    _detail("Summary validation failed")
                    upload_session.processing_status = UploadSession.STATUS_FAILED
                    upload_session.processing_error = "Summary validation failed: missing required sections"
                    close_old_connections()
                    upload_session.save(update_fields=["processing_status", "processing_error"])
                    result = GenerationResult(
                        success=False,
                        error="Summary validation failed",
                        generation_type=GenerationType.SUMMARY,
                        duration_ms=result.duration_ms,
                        provider_name=result.provider_name,

                    )
            elif result:
                _detail(f"AI call failed: {result.error}")

    # Instrument GenerationMetric at the very end
    if result is not None:
        output_length = len(result.data) if result.success and isinstance(result.data, str) else 0
        output_validated = result.success and _validate_summary(result.data) if result.success else False
        close_old_connections()
        GenerationMetric.objects.create(
            generation_type='summary',
            provider=result.provider_name or '',
            success=result.success,
            duration_ms=result.duration_ms,
            cache_hit=cache_hit,
            error_message=(result.error or '')[:500] if not result.success else '',
            output_length=output_length,
            related_session=upload_session if isinstance(upload_session, UploadSession) else None,
            output_validated=output_validated,
        )

    return result if result is not None else GenerationResult(
        success=False,
        error="Unknown error in summary processing.",
        generation_type=GenerationType.SUMMARY,
    )


def _map_reduce_summary(
    upload_session: UploadSession,
    all_text: str,
    chapter_title: str,
    provider: ModelProvider,
    _detail,
    rag_context: str = "",
    cross_ref: str = "N/A",
) -> GenerationResult:
    """Two-pass summary: extract concepts from sections IN PARALLEL, then synthesize with RAG context."""
    start_total = timezone.now()
    total_len = len(all_text)

    def _fallback_summary_from_concepts(concepts_text: str) -> str:
        compact = " ".join(concepts_text.split())[:3000]
        return (
            "## Study Summary\n"
            "### Overview\n"
            f"The material for **{chapter_title}** covers several programming ideas extracted from the uploaded document. "
            "The summary below is based on the successfully extracted concept notes because the final AI synthesis returned an empty response.\n\n"
            "### Key Concepts\n"
            f"- **Extracted concepts**: {compact}\n"
            "- **Programming focus**: Review any `code`, syntax, definitions, examples, and process steps mentioned in the extracted notes.\n\n"
            "### Review Focus\n"
            "- **Re-read weak sections**: Focus on the page sections represented in the extracted concepts.\n"
            "- **Practice actively**: Turn each major concept into a short explanation, example, or `code` exercise.\n"
        )

    # Calculate number of chunks based on document length (capped at 4)
    num_chunks = min(4, max(2, total_len // 25000))
    chunk_size = 12000

    # Skip first 3% to avoid title/copyright pages
    usable_start = int(total_len * 0.03)
    usable_end = total_len - chunk_size

    if usable_end <= usable_start:
        positions = [usable_start]
    else:
        step = (usable_end - usable_start) // max(num_chunks - 1, 1)
        positions = [usable_start + step * i for i in range(num_chunks)]

    _detail(
        f"Document is long ({total_len} chars) — using map-reduce with {len(positions)} sections (PARALLEL)..."
    )

    # ── MAP PHASE: Extract concepts from each section IN PARALLEL ─────────────
    concept_notes = [None] * len(positions)

    def _extract_worker(args):
        i, pos = args
        chunk = all_text[pos : pos + chunk_size]
        pct = int((pos / total_len) * 100)
        _detail(f"MAP [{i + 1}/{len(positions)}] extracting concepts from ~{pct}% of document...")
        try:
            concepts = provider.extract_concepts(chunk)
            return i, pct, concepts, None
        except Exception as e:
            return i, pct, None, str(e)

    with ThreadPoolExecutor(max_workers=min(4, len(positions))) as executor:
        futures = [executor.submit(_extract_worker, (i, pos)) for i, pos in enumerate(positions)]
        for future in as_completed(futures):
            try:
                i, pct, concepts, error = future.result()
                if concepts:
                    concept_notes[i] = (
                        f"=== SECTION {i + 1} (position {pct}%) ===\n{concepts}\n"
                    )
                    _detail(f"MAP [{i + 1}/{len(positions)}] extracted {len(concepts)} chars")
                else:
                    _detail(f"MAP [{i + 1}/{len(positions)}] failed: {error}")
            except Exception as exc:
                _detail(f"MAP worker crashed: {exc}")

    # Filter out failed (None) entries
    concept_notes = [n for n in concept_notes if n is not None]

    if not concept_notes:
        total_duration = (timezone.now() - start_total).total_seconds() * 1000
        return GenerationResult(
            success=False,
            error="All concept extraction (map) calls failed.",
            generation_type=GenerationType.SUMMARY,
            duration_ms=total_duration,
        )

    # ── REDUCE PHASE: Synthesize all concept notes into final summary ─────────
    combined_notes = "\n".join(concept_notes)
    _detail(f"REDUCE: synthesizing {len(combined_notes)} chars of extracted concepts with RAG context...")

    reduce_context = rag_context if rag_context else combined_notes
    try:
        final_summary = provider.generate_summary(reduce_context, chapter_title, cross_ref)
        _detail(f"REDUCE: final summary received ({len(final_summary)} chars)")
        total_duration = (timezone.now() - start_total).total_seconds() * 1000
        return GenerationResult(
            success=True,
            data=final_summary,
            generation_type=GenerationType.SUMMARY,
            duration_ms=total_duration,
            provider_name=provider.get_provider_name(),
        )
    except Exception as e:
        _detail(f"REDUCE: synthesis failed: {e}; retrying with shorter context...")

    try:
        final_summary = provider.generate_summary(reduce_context[:5000], chapter_title, cross_ref)
        _detail(f"REDUCE RETRY: final summary received ({len(final_summary)} chars)")
    except Exception as retry_error:
        _detail(f"REDUCE RETRY: synthesis failed: {retry_error}; using concept fallback summary.")
        final_summary = _fallback_summary_from_concepts(combined_notes)

    total_duration = (timezone.now() - start_total).total_seconds() * 1000
    return GenerationResult(
        success=True,
        data=final_summary,
        generation_type=GenerationType.SUMMARY,
        duration_ms=total_duration,
        provider_name=provider.get_provider_name(),
    )


def _process_quiz_for_session(upload_session) -> GenerationResult:
    from .topic_service import find_topic_for_chapter

    start_time = timezone.now()

    if isinstance(upload_session, int):
        upload_session = UploadSession.objects.select_related("chapter").get(id=upload_session)

    chapter = upload_session.chapter
    result = None
    cache_hit = False
    quiz = None
    output_length = 0

    if not chapter:
        result = GenerationResult(
            success=False,
            error="Quiz has no associated chapter.",
            generation_type=GenerationType.QUIZ,
        )
    else:
        # ── Lock the most recent quiz for this session to prevent race conditions ──
        with transaction.atomic():
            quiz = (
                Quiz.objects
                .filter(upload_session=upload_session)
                .select_for_update(nowait=False)
                .order_by("-created_at")
                .first()
            )
            if not quiz:
                primary_file = upload_session.files.order_by("id").first()
                quiz = Quiz.objects.create(
                    chapter=chapter,
                    upload_session=upload_session,
                    uploaded_file=primary_file,
                    status=Quiz.STATUS_PROCESSING,
                )
            elif quiz.status == Quiz.STATUS_COMPLETED:
                logger.info("[QUIZ] Quiz %s already completed for session %s — returning existing.", quiz.id, upload_session.id)
                result = GenerationResult(
                    success=True,
                    data=quiz,
                    generation_type=GenerationType.QUIZ,
                )
            else:
                quiz.status = Quiz.STATUS_PROCESSING
                quiz.save(update_fields=["status"])

        if result is None:
            generation_occurred = True

            def _fail(error_msg: str, original_result: GenerationResult = None) -> GenerationResult:
                """Mark quiz as FAILED and return a failed GenerationResult."""
                if quiz.status in {Quiz.STATUS_PENDING, Quiz.STATUS_PROCESSING}:
                    quiz.status = Quiz.STATUS_FAILED
                    quiz.error_message = error_msg[:500]
                    quiz.save(update_fields=["status", "error_message"])
                return GenerationResult(
                    success=False,
                    error=error_msg,
                    generation_type=GenerationType.QUIZ,
                    duration_ms=original_result.duration_ms if original_result else 0,
                    provider_name=original_result.provider_name if original_result else None,
                )

            def _detail(msg):
                logger.info("[QUIZ SESSION %s] %s", upload_session.id, msg)

            # Use RAG retrieval for quiz context (also logs to RetrievalLog)
            _detail("Retrieving context via RAG for quiz generation...")
            rag_result = retrieve_context_for_session(upload_session, mode='quiz')
            context_text = rag_result['context_text']
            cross_ref = rag_result['cross_reference_notes']

            if not context_text:
                # Fallback: build minimal context from uploaded chunks if RAG returns nothing
                raw_session_chunks = list(
                    UploadedChunk.objects.filter(upload_session=upload_session)
                    .only("id", "content")
                    .order_by("chunk_index")[:60]
                )
                if not raw_session_chunks:
                    result = _fail("No uploaded chunks available for quiz generation.")
                else:
                    context_text = "\n\n".join(f"- {chunk.content}" for chunk in raw_session_chunks[:8])
                    cross_ref = "N/A"
                    _detail(f"RAG returned empty — using {len(raw_session_chunks[:8])} direct chunks as fallback")

            if result is None:
                rag_context = GenerationContext(
                    context_text=context_text,
                    cross_reference_notes=cross_ref,
                )

                provider = get_generation_provider()

                quiz_cache_key = _cache_key(
                    "quiz", chapter.id, _get_content_hash(rag_context.context_text + chapter.title)
                )
                cached_quiz_data = cache.get(quiz_cache_key)
                if cached_quiz_data:
                    cache_hit = True
                    logger.info("[QUIZ] Cache hit for session %s — skipping AI call.", upload_session.id)
                    result = GenerationResult(
                        success=True,
                        data=cached_quiz_data,
                        generation_type=GenerationType.QUIZ,
                        provider_name='cache',
                    )
                else:
                    try:
                        quiz_data = provider.generate_mcq(
                            rag_context.context_text,
                            chapter.title,
                            rag_context.cross_reference_notes,
                        )
                        result = GenerationResult(
                            success=True,
                            data=quiz_data,
                            generation_type=GenerationType.QUIZ,
                            provider_name=provider.get_provider_name(),
                        )
                    except Exception as e:
                        _detail(f"AI quiz generation failed: {e}")
                        result = GenerationResult(
                            success=False,
                            error=str(e),
                            generation_type=GenerationType.QUIZ,
                            provider_name=provider.get_provider_name(),
                        )
                    if result.success and result.data:
                        cache.set(quiz_cache_key, result.data, timeout=60 * 60 * 24 * 7)

                if result and result.success and result.data:
                    # ── VALIDATE quiz data BEFORE creating questions ──
                    validation_errors = []
                    for i, mcq in enumerate(result.data[:10]):
                        q_text = mcq.get('question', '')
                        if not isinstance(q_text, str) or len(q_text) <= 10:
                            validation_errors.append(f"Q{i+1}: question missing or too short")
                        choices = mcq.get('choices') or {}
                        if not isinstance(choices, dict) or not all(isinstance(choices.get(k), str) and choices.get(k) for k in ['A', 'B', 'C', 'D']):
                            validation_errors.append(f"Q{i+1}: invalid choices")
                        if mcq.get('correct_answer') not in {'A', 'B', 'C', 'D'}:
                            validation_errors.append(f"Q{i+1}: invalid correct_answer")
                        topic = mcq.get('topic', '')
                        if not isinstance(topic, str) or not topic:
                            validation_errors.append(f"Q{i+1}: topic missing")

                    if validation_errors:
                        result = _fail(f"Quiz validation failed: {', '.join(validation_errors)}", original_result=result)
                    else:
                        quiz_json = json.dumps(result.data[:10])
                        output_length = len(quiz_json)

                        primary_file = upload_session.files.order_by("id").first()
                        existing_topics = list(Topic.objects.filter(chapter=chapter))
                        questions_to_create = []

                        for mcq in result.data[:10]:
                            choices = mcq.get("choices") or {}
                            topic = find_topic_for_chapter(chapter, mcq.get("topic", ""), create=True, existing_topics=existing_topics)
                            questions_to_create.append(
                                Question(
                                    chapter=chapter,
                                    topic=topic,
                                    uploaded_file=primary_file,
                                    text=mcq["question"],
                                    choice_a=choices.get("A", ""),
                                    choice_b=choices.get("B", ""),
                                    choice_c=choices.get("C", ""),
                                    choice_d=choices.get("D", ""),
                                    correct_answer=mcq["correct_answer"],
                                )
                            )

                        with transaction.atomic():
                            quiz.questions.clear()
                            created_questions = Question.objects.bulk_create(questions_to_create)
                            quiz.questions.add(*created_questions)

                            quiz.status = Quiz.STATUS_COMPLETED
                            quiz.generated_at = timezone.now()
                            quiz.save(update_fields=["status", "generated_at"])

                        logger.info(
                            "Quiz %s generated for upload session %s with %s questions.",
                            quiz.id,
                            upload_session.id,
                            len(result.data[:10]),
                        )
                        result = GenerationResult(
                            success=True,
                            data=quiz,
                            generation_type=GenerationType.QUIZ,
                            duration_ms=result.duration_ms,
                            provider_name=result.provider_name,
    
                        )
                elif result and result.success and not result.data:
                    result = _fail("AI quiz generation returned no data.", original_result=result)
                elif result and not result.success:
                    result = _fail(result.error or "AI quiz generation failed.", original_result=result)

    # Instrument GenerationMetric at the very end
    if result is not None:
        duration_ms = result.duration_ms or 0
        wall_clock_ms = (timezone.now() - start_time).total_seconds() * 1000
        final_duration = max(duration_ms, wall_clock_ms)

        if output_length == 0 and result.success and result.data:
            if isinstance(result.data, list):
                output_length = len(json.dumps(result.data))
            elif isinstance(result.data, str):
                output_length = len(result.data)
            elif hasattr(result.data, 'questions'):
                output_length = result.data.questions.count()

        output_validated = False
        if result.success and result.generation_type == GenerationType.QUIZ:
            if isinstance(result.data, list) and len(result.data) > 0:
                # Validate each MCQ has required fields
                valid_count = 0
                for mcq in result.data[:10]:
                    q_text = mcq.get('question', '')
                    choices = mcq.get('choices') or {}
                    has_valid_choices = all(isinstance(choices.get(k), str) and choices.get(k) for k in ['A', 'B', 'C', 'D'])
                    has_valid_answer = mcq.get('correct_answer') in {'A', 'B', 'C', 'D'}
                    has_topic = isinstance(mcq.get('topic', ''), str) and mcq.get('topic', '')
                    if isinstance(q_text, str) and len(q_text) > 10 and has_valid_choices and has_valid_answer and has_topic:
                        valid_count += 1
                output_validated = valid_count >= 5  # At least 5 of 10 questions must be valid
            elif hasattr(result.data, 'questions') and result.data.questions.count() > 0:
                output_validated = True

        GenerationMetric.objects.create(
            generation_type='quiz',
            provider=result.provider_name or '',
            success=result.success,
            duration_ms=final_duration,
            cache_hit=cache_hit,
            error_message=(result.error or '')[:500] if not result.success else '',
            output_length=output_length,
            related_session=upload_session if isinstance(upload_session, UploadSession) else None,
            output_validated=output_validated,
        )

    return result if result is not None else GenerationResult(
        success=False,
        error="Unknown error in quiz processing.",
        generation_type=GenerationType.QUIZ,
    )


def _process_recommendations_for_attempt(
    attempt: QuizAttempt,
) -> GenerationResult:
    answers = attempt.answers.select_related("question", "question__topic").all()
    questions_with_answers = [
        {
            "question": answer.question.text,
            "selected_answer": answer.selected_answer,
            "correct_answer": answer.question.correct_answer,
            "is_correct": answer.is_correct,
            "topic": answer.question.topic.title if answer.question.topic else "General",
        }
        for answer in answers
    ]

    # Build deterministic cache key from answers
    answer_signature = "|".join(
        f"{qa['question'][:40]}:{qa['selected_answer']}:{qa['is_correct']}"
        for qa in questions_with_answers
    )
    rec_cache_key = _cache_key(
        "recommendations", attempt.quiz.chapter_id or 0, _get_content_hash(answer_signature)
    )
    cached_recommendation = cache.get(rec_cache_key)
    cache_hit = False
    result = None

    if cached_recommendation:
        cache_hit = True
        logger.info("[REC] Cache hit for attempt %s — returning cached recommendation.", attempt.id)
        attempt.ai_recommendation = cached_recommendation
        attempt.recommendation_status = QuizAttempt.RECOMMENDATION_COMPLETED
        attempt.save(update_fields=["ai_recommendation", "recommendation_status"])
        result = GenerationResult(
            success=True,
            data=cached_recommendation,
            generation_type=GenerationType.RECOMMENDATIONS,
            provider_name='cache',
        )
    else:
        provider = get_generation_provider()
        try:
            recommendation_text = provider.generate_recommendations(attempt, questions_with_answers)
            result = GenerationResult(
                success=True,
                data=recommendation_text,
                generation_type=GenerationType.RECOMMENDATIONS,
                provider_name=provider.get_provider_name(),
            )
        except Exception as e:
            logger.error("[REC] AI recommendation failed: %s", e)
            result = GenerationResult(
                success=False,
                error=str(e),
                generation_type=GenerationType.RECOMMENDATIONS,
                provider_name=provider.get_provider_name(),
            )

        if result and result.success:
            if isinstance(result.data, str) and len(result.data) > 50:
                attempt.ai_recommendation = result.data
                attempt.recommendation_status = QuizAttempt.RECOMMENDATION_COMPLETED
                attempt.save(update_fields=["ai_recommendation", "recommendation_status"])
                cache.set(rec_cache_key, result.data, timeout=60 * 60 * 24 * 7)
                result = GenerationResult(
                    success=True,
                    data=result.data,
                    generation_type=GenerationType.RECOMMENDATIONS,
                    duration_ms=result.duration_ms,
                    provider_name=result.provider_name,
                )
            else:
                error_msg = "Recommendation validation failed: output too short"
                attempt.recommendation_status = QuizAttempt.RECOMMENDATION_FAILED
                attempt.recommendation_error = error_msg
                attempt.save(update_fields=["recommendation_status", "recommendation_error"])
                result = GenerationResult(
                    success=False,
                    error=error_msg,
                    generation_type=GenerationType.RECOMMENDATIONS,
                    duration_ms=result.duration_ms,
                    provider_name=result.provider_name,
                )

    # Instrument GenerationMetric at the very end
    if result is not None:
        output_length = len(result.data) if result.success and isinstance(result.data, str) else 0
        output_validated = False
        if result.success and result.generation_type == GenerationType.RECOMMENDATIONS:
            output_validated = isinstance(result.data, str) and len(result.data) > 50

        GenerationMetric.objects.create(
            generation_type='recommendations',
            provider=result.provider_name or '',
            success=result.success,
            duration_ms=result.duration_ms,
            cache_hit=cache_hit,
            error_message=(result.error or '')[:500] if not result.success else '',
            output_length=output_length,
            related_attempt=attempt,
            output_validated=output_validated,
        )

    return result if result is not None else GenerationResult(
        success=False,
        error="Unknown error in recommendation processing.",
        generation_type=GenerationType.RECOMMENDATIONS,
    )


def process_upload_session_simple(upload_session_id: int) -> dict:
    """
    Simplified upload processing pipeline with detailed timing.

    Logs every step to quizsense_processing.log with durations.
    """
    from .timing_logger import ProcessingTimer
    from .memory_monitor import log_memory
    import gc

    log_memory(f"[SESSION {upload_session_id}] START")
    logger.info("[SESSION %s] Starting upload processing", upload_session_id)

    upload_session = UploadSession.objects.select_related("chapter").get(id=upload_session_id)
    upload_session.processing_status = UploadSession.STATUS_PROCESSING
    upload_session.processing_error = ""
    upload_session.processing_started_at = timezone.now()
    upload_session.processing_completed_at = None
    upload_session.save(
        update_fields=[
            "processing_status",
            "processing_error",
            "processing_started_at",
            "processing_completed_at",
        ]
    )

    results = {"summary": None, "quiz": None}
    timings = {}

    try:
        # Step 1: Extract text from files
        with ProcessingTimer(upload_session_id, "TEXT_EXTRACTION") as timer:
            files = list(upload_session.files.all().order_by("id"))
            timer.detail(f"Found {len(files)} files")

            for file in files:
                filename = file.file.name.lower()
                timer.detail(f"Extracting: {filename}")
                file.file.open("rb")
                try:
                    if filename.endswith(".pdf"):
                        from .file_processor import extract_text_from_pdf
                        extracted_text = extract_text_from_pdf(file.file)
                    elif filename.endswith(".docx"):
                        from .file_processor import extract_text_from_docx
                        extracted_text = extract_text_from_docx(file.file)
                    else:
                        timer.detail(f"Skipping unsupported: {filename}")
                        continue
                finally:
                    file.file.close()

                # Sanitize NUL characters (PostgreSQL cannot store them in text fields)
                file.extracted_text = extracted_text.replace("\x00", "") if extracted_text else ""
                # Long OCR/API calls can leave PostgreSQL connections stale.
                close_old_connections()
                file.save(update_fields=["extracted_text"])
                extracted_len = len(extracted_text or '')
                del extracted_text  # Free RAM immediately
                timer.detail(f"Extracted {extracted_len} chars from {filename}")

        timings["TEXT_EXTRACTION"] = time.time() - upload_session.processing_started_at.timestamp()

        # Step 2: Create chunks and embeddings
        with ProcessingTimer(upload_session_id, "CHUNKING_EMBEDDING") as timer:
            all_chunks_to_create = []
            for file in upload_session.files.all():
                if file.extracted_text and file.extracted_text.strip():
                    chunks = split_text_into_chunks(file.extracted_text)
                    timer.detail(f"{file.file.name}: {len(chunks)} chunks")
                    if chunks:
                        embeddings = embed_texts_batched(chunks)
                        timer.detail(f"{file.file.name}: {len(embeddings)} embeddings")
                        for index, (chunk_text, embedding) in enumerate(zip(chunks, embeddings)):
                            all_chunks_to_create.append(
                                UploadedChunk(
                                    upload_session=upload_session,
                                    uploaded_file=file,
                                    chapter=file.chapter,
                                    chunk_index=index,
                                    content=chunk_text,
                                    embedding=embedding,
                                )
                            )
            if all_chunks_to_create:
                close_old_connections()
                UploadedChunk.objects.bulk_create(
                    all_chunks_to_create,
                    batch_size=500,
                    ignore_conflicts=True,
                )
                timer.detail(f"Bulk created {len(all_chunks_to_create)} chunks")
                del all_chunks_to_create

        timings["CHUNKING_EMBEDDING"] = time.time() - upload_session.processing_started_at.timestamp() - timings.get("TEXT_EXTRACTION", 0)

        # Step 3: Generate summary (RAG + AI)
        with ProcessingTimer(upload_session_id, "SUMMARY_GENERATION") as timer:
            summary_result = _process_summary_for_session(upload_session, timer=timer)
            results["summary"] = summary_result

        timings["SUMMARY_GENERATION"] = time.time() - upload_session.processing_started_at.timestamp() - timings.get("TEXT_EXTRACTION", 0) - timings.get("CHUNKING_EMBEDDING", 0)

        total_elapsed = time.time() - upload_session.processing_started_at.timestamp()

        if summary_result.success:
            logger.info("[SESSION %s] Summary generated successfully in %.1fs", upload_session_id, total_elapsed)
            upload_session.summary = summary_result.data
            upload_session.processing_status = UploadSession.STATUS_COMPLETED
            upload_session.processing_completed_at = timezone.now()
            close_old_connections()
            upload_session.save(
                update_fields=[
                    "summary",
                    "processing_status",
                    "processing_completed_at",
                ]
            )

            # OPTION A: Pre-generate quiz immediately so it's ready when the user clicks "Start Quiz"
            logger.info("[SESSION %s] Auto-queueing quiz generation...", upload_session_id)
            queue_quiz_generation(upload_session_id)
        else:
            logger.error("[SESSION %s] Summary generation failed: %s", upload_session_id, summary_result.error)
            upload_session.processing_status = UploadSession.STATUS_FAILED
            upload_session.processing_error = summary_result.error
            upload_session.processing_completed_at = timezone.now()
            close_old_connections()
            upload_session.save(
                update_fields=[
                    "processing_status",
                    "processing_error",
                    "processing_completed_at",
                ]
            )

        logger.info("[SESSION %s] Processing completed in %.1fs", upload_session_id, total_elapsed)
        log_memory(f"[SESSION {upload_session_id}] END")
        # Run GC after large PDF strings/chunks are released.
        gc.collect()
        return results

    except Exception as exc:
        elapsed = time.time() - upload_session.processing_started_at.timestamp()
        logger.exception("[SESSION %s] Upload session processing failed after %.1fs: %s", upload_session_id, elapsed, exc)
        upload_session.processing_status = UploadSession.STATUS_FAILED
        upload_session.processing_error = str(exc)
        upload_session.processing_completed_at = timezone.now()
        close_old_connections()
        upload_session.save(
            update_fields=[
                "processing_status",
                "processing_error",
                "processing_completed_at",
            ]
        )
        log_memory(f"[SESSION {upload_session_id}] FAILED")
        # Run GC after large PDF strings/chunks are released.
        gc.collect()
        return {
            "summary": GenerationResult(
                success=False, error=str(exc), generation_type=GenerationType.SUMMARY
            ),
            "quiz": None,
        }


def queue_upload_session_processing(upload_session_id: int) -> None:
    def dispatch():
        try:
            from ..tasks import process_upload_session_task
            process_upload_session_task.delay(upload_session_id)
        except Exception as exc:
            logger.warning(
                "Celery unavailable for upload session %s: %s",
                upload_session_id,
                exc,
            )
            worker = threading.Thread(
                target=_process_upload_session_thread,
                args=(upload_session_id,),
                daemon=True,
                name=f"upload-session-{upload_session_id}",
            )
            worker.start()

    transaction.on_commit(dispatch)


def _process_upload_session_thread(upload_session_id: int) -> None:
    close_old_connections()
    try:
        process_upload_session_simple(upload_session_id)
    except Exception as exc:
        logger.exception("[SESSION %s] Thread processing failed: %s", upload_session_id, exc)
        try:
            from ..models import UploadSession
            upload_session = UploadSession.objects.get(id=upload_session_id)
            upload_session.processing_status = UploadSession.STATUS_FAILED
            upload_session.processing_error = f"Thread error: {str(exc)[:500]}"
            upload_session.processing_completed_at = timezone.now()
            upload_session.save(
                update_fields=["processing_status", "processing_error", "processing_completed_at"]
            )
        except Exception as inner_exc:
            logger.error("[SESSION %s] Failed to update error status: %s", upload_session_id, inner_exc)
    finally:
        close_old_connections()


def queue_quiz_generation(upload_session_id: int) -> None:
    """Queue quiz generation via Celery, with fallback to background thread."""
    def dispatch():
        logger.info("[QUIZ-AUTO] Dispatching quiz generation for session %s", upload_session_id)
        try:
            from ..tasks import generate_quiz_task
            result = generate_quiz_task.delay(upload_session_id)
            logger.info("[QUIZ-AUTO] Quiz task dispatched: task_id=%s", result.id)
        except Exception as exc:
            logger.warning(
                "[QUIZ-AUTO] Celery unavailable for quiz generation on upload session %s: %s. Using fallback thread.",
                upload_session_id,
                exc,
            )
            worker = threading.Thread(
                target=_generate_quiz_thread,
                args=(upload_session_id,),
                daemon=True,
                name=f"quiz-generation-{upload_session_id}",
            )
            worker.start()
            logger.info("[QUIZ-AUTO] Fallback thread started for session %s", upload_session_id)

    # CRITICAL FIX: When called from inside a Celery task (like process_upload_session_task),
    # transaction.on_commit() may not fire because Celery tasks don't run inside a Django
    # transaction by default. We detect this and dispatch immediately.
    from django.db import connection
    if connection.in_atomic_block:
        logger.info("[QUIZ-AUTO] Inside DB transaction, using on_commit for session %s", upload_session_id)
        transaction.on_commit(dispatch)
    else:
        logger.info("[QUIZ-AUTO] No active DB transaction, dispatching immediately for session %s", upload_session_id)
        dispatch()


def _generate_quiz_thread(upload_session_id: int) -> None:
    close_old_connections()
    try:
        _process_quiz_for_session(upload_session_id)
    except Exception as exc:
        logger.exception("[QUIZ] Thread generation failed for session %s: %s", upload_session_id, exc)
        try:
            from ..models import UploadSession, Quiz
            upload_session = UploadSession.objects.get(id=upload_session_id)
            quiz = Quiz.objects.filter(upload_session=upload_session).order_by("-created_at").first()
            if quiz:
                quiz.status = Quiz.STATUS_FAILED
                quiz.error_message = f"Thread error: {str(exc)[:500]}"
                quiz.save(update_fields=["status", "error_message"])
        except Exception as inner_exc:
            logger.error("[QUIZ] Failed to update error status for session %s: %s", upload_session_id, inner_exc)
    finally:
        close_old_connections()


def generate_recommendations_for_attempt(attempt_id: int) -> GenerationResult:
    attempt = QuizAttempt.objects.select_related("quiz", "quiz__chapter").get(id=attempt_id)

    # Early exit if already completed
    if attempt.recommendation_status == QuizAttempt.RECOMMENDATION_COMPLETED and attempt.ai_recommendation:
        return GenerationResult(
            success=True,
            data=attempt.ai_recommendation,
            generation_type=GenerationType.RECOMMENDATIONS,
            provider_name='existing',
        )

    # Early exit if already processing (another task is handling it)
    if attempt.recommendation_status == QuizAttempt.RECOMMENDATION_PROCESSING:
        return GenerationResult(
            success=False,
            error="Recommendations already being generated",
            generation_type=GenerationType.RECOMMENDATIONS,
        )

    attempt.recommendation_status = QuizAttempt.RECOMMENDATION_PROCESSING
    attempt.recommendation_error = ""
    attempt.save(update_fields=["recommendation_status", "recommendation_error"])

    try:
        result = _process_recommendations_for_attempt(attempt)
        if result.success:
            return result
        else:
            attempt.recommendation_status = QuizAttempt.RECOMMENDATION_FAILED
            attempt.recommendation_error = result.error or "Unknown error"
            attempt.save(update_fields=["recommendation_status", "recommendation_error"])
            return result
    except Exception as exc:
        logger.exception("Recommendations generation failed for attempt %s", attempt_id)
        attempt.recommendation_status = QuizAttempt.RECOMMENDATION_FAILED
        attempt.recommendation_error = str(exc)
        attempt.save(update_fields=["recommendation_status", "recommendation_error"])
        return GenerationResult(
            success=False,
            error=str(exc),
            generation_type=GenerationType.RECOMMENDATIONS,
        )


def queue_recommendations_generation(attempt_id: int) -> None:
    # Prevent duplicate queuing if recommendations already completed
    attempt = QuizAttempt.objects.only("recommendation_status", "ai_recommendation").get(id=attempt_id)
    if attempt.recommendation_status == QuizAttempt.RECOMMENDATION_COMPLETED and attempt.ai_recommendation:
        return

    def dispatch():
        try:
            from ..tasks import generate_recommendations_task
            generate_recommendations_task.delay(attempt_id)
        except Exception as exc:
            logger.warning(
                "Celery unavailable for recommendations on attempt %s: %s",
                attempt_id,
                exc,
            )
            worker = threading.Thread(
                target=_generate_recommendations_thread,
                args=(attempt_id,),
                daemon=True,
                name=f"recommendations-{attempt_id}",
            )
            worker.start()

    # When called from inside a Celery task or view without an explicit
    # transaction, on_commit may not fire. Detect and dispatch immediately.
    from django.db import connection
    if connection.in_atomic_block:
        transaction.on_commit(dispatch)
    else:
        dispatch()


def _generate_recommendations_thread(attempt_id: int) -> None:
    close_old_connections()
    try:
        generate_recommendations_for_attempt(attempt_id)
    finally:
        close_old_connections()
