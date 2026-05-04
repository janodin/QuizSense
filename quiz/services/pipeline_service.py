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

All use MiniMax M2.7 as primary AI provider with Gemini fallback.
"""

import hashlib
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


class GeminiProvider(AIProvider):
    def __init__(self):
        self._client = None
        self._client_lock = threading.Lock()
        self._semaphore = threading.Semaphore(4)

    def _get_client(self):
        if self._client is None:
            with self._client_lock:
                if self._client is None:
                    import google.genai as genai
                    from django.conf import settings

                    self._client = genai.Client(api_key=settings.GOOGLE_API_KEY)
        return self._client

    def _chat(self, prompt: str, max_tokens: int = 1024) -> str:
        from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception

        def _is_503(exc):
            if hasattr(exc, '__cause__'):
                cause = exc.__cause__
                if hasattr(cause, 'args') and cause.args:
                    args = cause.args[0] if isinstance(cause.args[0], dict) else {}
                    if isinstance(args, dict):
                        return args.get('status') == 'UNAVAILABLE'
            return False

        @retry(
            stop=stop_after_attempt(2),
            wait=wait_exponential(multiplier=1, min=2, max=15),
            retry=retry_if_exception(_is_503),
            reraise=True,
        )
        def _call():
            self._semaphore.acquire()
            try:
                client = self._get_client()
                response = client.models.generate_content(
                    model="gemini-2.5-flash-lite",
                    contents=prompt,
                    config={"max_output_tokens": max_tokens},
                )
                return response.text or ""
            finally:
                self._semaphore.release()

        return _call()

    def generate_summary(
        self, text: str, chapter_title: str, cross_reference_notes: str
    ) -> str:
        prompt = (
            "You are an expert programming instructor. Based on the context below, "
            "write a polished, richly-formatted study summary in 180-260 words.\n\n"
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
        return self._chat(prompt, max_tokens=1024)

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
        return self._chat(prompt, max_tokens=1024)

    def generate_mcq(
        self, text: str, chapter_title: str, cross_reference_notes: str
    ) -> list:
        import json
        import re

        prompt = (
            "You are an expert programming instructor. Generate exactly 10 MCQs as a JSON array.\n\n"
            f"Chapter: {chapter_title}\n\n"
            f"Retrieved Context:\n{text[:6000]}\n\n"
            f"Cross-Reference Notes (textbook topic matches):\n{cross_reference_notes or 'N/A'}\n\n"
            'Return ONLY a valid JSON array. Each object: {{"question", "choices":{{"A","B","C","D"}}, "correct_answer", "topic"}}'
        )
        raw = self._chat(prompt, max_tokens=4096)

        cleaned = re.sub(r"```(?:json)?", "", raw).strip().strip("`")
        start = cleaned.find("[")
        end = cleaned.rfind("]")
        if start == -1 or end == -1:
            raise ValueError(f"AI Response parsing failed. Raw starts with: {raw[:50]}")

        questions = json.loads(cleaned[start:end + 1])
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
            f"You are an expert programming instructor. Provide 3-4 concise, actionable study recommendations "
            f"for a student who scored {quiz_attempt.score}/{quiz_attempt.total_questions} on \"{chapter_title}\".\n\n"
            f"--- All Questions ---\n{topic_summary}\n\n"
            f"--- Incorrect Answers ---\n{wrong_summary}\n\n"
            f"Focus on the weak topics and explain what the student should study to improve."
        )
        return self._chat(prompt, max_tokens=1024)

    def get_provider_name(self) -> str:
        return "gemini"


class MiniMaxProvider(AIProvider):
    def __init__(self):
        self._url = "https://api.minimax.io/anthropic/v1/messages"
        self._model = "MiniMax-M2.7"

    def _make_request(self, prompt: str, max_tokens: int = 1024, timeout: int = 60) -> str:
        import json
        import time
        import requests
        from django.conf import settings

        start_time = time.time()
        logger.info("[MINIMAX] Sending request (prompt_length=%d, max_tokens=%d, timeout=%d)", len(prompt), max_tokens, timeout)

        headers = {
            "Authorization": f"Bearer {settings.MINIMAX_API_KEY}",
            "Content-Type": "application/json",
            "anthropic-version": "2023-06-01",
            "anthropic-dangerous-direct-browser-access": "true",
        }
        payload = {
            "model": self._model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
        }

        try:
            response = requests.post(self._url, headers=headers, json=payload, timeout=timeout)
            elapsed = time.time() - start_time
            logger.info("[MINIMAX] Response received in %.1fs (status=%d)", elapsed, response.status_code)
        except requests.exceptions.Timeout:
            logger.error("[MINIMAX] Request timed out after %ds", timeout)
            raise ValueError(f"MiniMax API request timed out after {timeout} seconds")
        except requests.exceptions.ConnectionError as e:
            logger.error("[MINIMAX] Connection error: %s", e)
            raise ValueError(f"MiniMax API connection error: {e}")

        if response.status_code != 200:
            try:
                error_data = response.json()
                msg = (
                    error_data.get("error", {}).get("message")
                    or error_data.get("base_resp", {}).get("status_msg")
                    or response.text
                )
            except Exception:
                msg = response.text
            raise ValueError(f"MiniMax HTTP {response.status_code}: {msg}")

        data = response.json()

        if "error" in data:
            msg = data["error"].get("message", str(data["error"]))
            raise ValueError(f"MiniMax API Error: {msg}")

        if "base_resp" in data and data["base_resp"].get("status_code") != 0:
            msg = data["base_resp"].get("status_msg", f"code {data['base_resp']['status_code']}")
            raise ValueError(f"MiniMax API Error: {msg}")

        if data.get("type") == "message":
            for block in data.get("content", []):
                if block.get("type") == "text":
                    return block["text"]
        return ""

    def generate_summary(
        self, text: str, chapter_title: str, cross_reference_notes: str
    ) -> str:
        prompt = (
            "You are an expert programming instructor. Based on the context below, "
            "write a polished, richly-formatted study summary in 180-260 words.\n\n"
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
        return self._make_request(prompt, max_tokens=1024)

    def generate_mcq(
        self, text: str, chapter_title: str, cross_reference_notes: str
    ) -> list:
        import json
        import re

        prompt = (
            "You are an expert programming instructor. Generate exactly 10 MCQs as a JSON array.\n\n"
            f"Chapter: {chapter_title}\n\n"
            f"Retrieved Context:\n{text[:12000]}\n\n"
            f"Cross-Reference Notes (textbook topic matches):\n{cross_reference_notes or 'N/A'}\n\n"
            'Return ONLY a valid JSON array. Each object: {{"question", "choices":{{"A","B","C","D"}}, "correct_answer", "topic"}}'
        )
        raw = self._make_request(prompt, max_tokens=4096, timeout=120)

        cleaned = re.sub(r"```(?:json)?", "", raw).strip().strip("`")
        start = cleaned.find("[")
        end = cleaned.rfind("]")
        if start == -1 or end == -1:
            raise ValueError(f"AI Response parsing failed. Raw starts with: {raw[:50]}")

        questions = json.loads(cleaned[start:end + 1])
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
            f"You are an expert programming instructor. Provide 3-4 concise, actionable study recommendations "
            f"for a student who scored {quiz_attempt.score}/{quiz_attempt.total_questions} on \"{chapter_title}\".\n\n"
            f"--- All Questions ---\n{topic_summary}\n\n"
            f"--- Incorrect Answers ---\n{wrong_summary}\n\n"
            f"Focus on the weak topics and explain what the student should study to improve."
        )
        return self._make_request(prompt)

    def get_provider_name(self) -> str:
        return "minimax"


class MultiProvider:
    def __init__(self):
        self._providers: list[AIProvider] = []

    def add_provider(self, provider: AIProvider) -> "MultiProvider":
        self._providers.append(provider)
        return self

    def generate_summary(
        self, text: str, chapter_title: str, cross_reference_notes: str
    ) -> GenerationResult:
        start = timezone.now()
        last_error = None

        for provider in self._providers:
            try:
                result = provider.generate_summary(text, chapter_title, cross_reference_notes)
                duration = (timezone.now() - start).total_seconds() * 1000
                return GenerationResult(
                    success=True,
                    data=result,
                    generation_type=GenerationType.SUMMARY,
                    duration_ms=duration,
                )
            except Exception as exc:
                logger.warning(
                    "Summary generation failed with %s: %s",
                    provider.get_provider_name(),
                    exc,
                )
                last_error = str(exc)
                continue

        duration = (timezone.now() - start).total_seconds() * 1000
        return GenerationResult(
            success=False,
            error=last_error,
            generation_type=GenerationType.SUMMARY,
            duration_ms=duration,
        )

    def generate_mcq(
        self, text: str, chapter_title: str, cross_reference_notes: str
    ) -> GenerationResult:
        start = timezone.now()
        last_error = None

        for provider in self._providers:
            try:
                result = provider.generate_mcq(text, chapter_title, cross_reference_notes)
                duration = (timezone.now() - start).total_seconds() * 1000
                return GenerationResult(
                    success=True,
                    data=result,
                    generation_type=GenerationType.QUIZ,
                    duration_ms=duration,
                )
            except Exception as exc:
                logger.warning(
                    "MCQ generation failed with %s: %s",
                    provider.get_provider_name(),
                    exc,
                )
                last_error = str(exc)
                continue

        duration = (timezone.now() - start).total_seconds() * 1000
        return GenerationResult(
            success=False,
            error=last_error,
            generation_type=GenerationType.QUIZ,
            duration_ms=duration,
        )

    def generate_recommendations(
        self, quiz_attempt: QuizAttempt, questions_with_answers: list
    ) -> GenerationResult:
        start = timezone.now()
        last_error = None

        for provider in self._providers:
            try:
                result = provider.generate_recommendations(quiz_attempt, questions_with_answers)
                duration = (timezone.now() - start).total_seconds() * 1000
                return GenerationResult(
                    success=True,
                    data=result,
                    generation_type=GenerationType.RECOMMENDATIONS,
                    duration_ms=duration,
                )
            except Exception as exc:
                logger.warning(
                    "Recommendations generation failed with %s: %s",
                    provider.get_provider_name(),
                    exc,
                )
                last_error = str(exc)
                continue

        duration = (timezone.now() - start).total_seconds() * 1000
        return GenerationResult(
            success=False,
            error=last_error,
            generation_type=GenerationType.RECOMMENDATIONS,
            duration_ms=duration,
        )

    def extract_concepts(self, text: str) -> GenerationResult:
        """Map-phase: extract key concepts from a chunk, trying each provider."""
        start = timezone.now()
        last_error = None

        for provider in self._providers:
            try:
                result = provider.extract_concepts(text)
                duration = (timezone.now() - start).total_seconds() * 1000
                return GenerationResult(
                    success=True,
                    data=result,
                    generation_type=GenerationType.SUMMARY,
                    duration_ms=duration,
                )
            except Exception as exc:
                logger.warning(
                    "Concept extraction failed with %s: %s",
                    provider.get_provider_name(),
                    exc,
                )
                last_error = str(exc)
                continue

        duration = (timezone.now() - start).total_seconds() * 1000
        return GenerationResult(
            success=False,
            error=last_error,
            generation_type=GenerationType.SUMMARY,
            duration_ms=duration,
        )


_default_provider: Optional[MultiProvider] = None
_provider_lock = threading.Lock()


def get_generation_provider() -> MultiProvider:
    global _default_provider
    if _default_provider is None:
        with _provider_lock:
            if _default_provider is None:
                _default_provider = MultiProvider().add_provider(
                    MiniMaxProvider()
                ).add_provider(GeminiProvider())
    return _default_provider


# ─── Cache helpers for AI-generated outputs ──────────────────────────────────


def _get_content_hash(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()[:16]


def _cache_key(prefix: str, chapter_id: Any, content_hash: str) -> str:
    return f"quizsense:{prefix}:ch{chapter_id}:{content_hash}"


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

    if not all_text.strip():
        return GenerationResult(
            success=False,
            error="No extracted text was available for summary generation.",
            generation_type=GenerationType.SUMMARY,
        )

    chapter_id = upload_session.chapter_id or 0
    cache_key = _cache_key("summary", chapter_id, _get_content_hash(all_text))
    cached_summary = cache.get(cache_key)
    if cached_summary:
        _detail("Summary cache hit — returning cached result.")
        upload_session.summary = cached_summary
        upload_session.save(update_fields=["summary"])
        return GenerationResult(
            success=True,
            data=cached_summary,
            generation_type=GenerationType.SUMMARY,
        )

    provider = get_generation_provider()
    total_len = len(all_text)

    # ── OPTION C: Map-Reduce for long documents ───────────────────────────────
    # For documents > 15K chars, we use a two-pass approach:
    #   MAP:   Extract key concepts from evenly-spaced sections (6 chunks max)
    #   REDUCE: Synthesize all concept lists into a polished summary
    # This covers the full document breadth without losing information in long context.
    # ──────────────────────────────────────────────────────────────────────────

    if total_len > 15000:
        return _map_reduce_summary(
            upload_session, all_text, chapter_title, provider, _detail
        )

    # ── Fast path: single-pass for short documents ────────────────────────────
    _detail(f"Document is short ({total_len} chars) — using single-pass summary...")
    combined_text = all_text[:12000]
    _detail(f"Context ready: {len(combined_text)} chars")

    _detail("Calling AI provider (MiniMax/Gemini)...")
    result = provider.generate_summary(combined_text, chapter_title, "N/A")

    if result.success:
        _detail(f"AI response received: {len(result.data)} chars")
        upload_session.summary = result.data
        upload_session.save(update_fields=["summary"])
        cache.set(cache_key, result.data, timeout=60 * 60 * 24 * 7)
    else:
        _detail(f"AI call failed: {result.error}")

    return result


def _map_reduce_summary(
    upload_session: UploadSession,
    all_text: str,
    chapter_title: str,
    provider: MultiProvider,
    _detail,
) -> GenerationResult:
    """Two-pass summary: extract concepts from sections IN PARALLEL, then synthesize."""
    total_len = len(all_text)

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
        map_result = provider.extract_concepts(chunk)
        return i, pct, map_result

    with ThreadPoolExecutor(max_workers=min(4, len(positions))) as executor:
        futures = [executor.submit(_extract_worker, (i, pos)) for i, pos in enumerate(positions)]
        for future in as_completed(futures):
            try:
                i, pct, map_result = future.result()
                if map_result.success:
                    concept_notes[i] = (
                        f"=== SECTION {i + 1} (position {pct}%) ===\n{map_result.data}\n"
                    )
                    _detail(f"MAP [{i + 1}/{len(positions)}] extracted {len(map_result.data)} chars")
                else:
                    _detail(f"MAP [{i + 1}/{len(positions)}] failed: {map_result.error}")
            except Exception as exc:
                _detail(f"MAP worker crashed: {exc}")

    # Filter out failed (None) entries
    concept_notes = [n for n in concept_notes if n is not None]

    if not concept_notes:
        return GenerationResult(
            success=False,
            error="All concept extraction (map) calls failed.",
            generation_type=GenerationType.SUMMARY,
        )

    # ── REDUCE PHASE: Synthesize all concept notes into final summary ─────────
    combined_notes = "\n".join(concept_notes)
    _detail(f"REDUCE: synthesizing {len(combined_notes)} chars of extracted concepts...")

    reduce_result = provider.generate_summary(combined_notes, chapter_title, "N/A")

    if reduce_result.success:
        _detail(f"REDUCE: final summary received ({len(reduce_result.data)} chars)")
        upload_session.summary = reduce_result.data
        upload_session.save(update_fields=["summary"])
        chapter_id = upload_session.chapter_id or 0
        cache_key = _cache_key("summary", chapter_id, _get_content_hash(all_text))
        cache.set(cache_key, reduce_result.data, timeout=60 * 60 * 24 * 7)
    else:
        _detail(f"REDUCE: synthesis failed: {reduce_result.error}")

    return reduce_result


def _process_quiz_for_session(upload_session) -> GenerationResult:
    from .topic_service import find_topic_for_chapter

    if isinstance(upload_session, int):
        upload_session = UploadSession.objects.select_related("chapter").get(id=upload_session)

    chapter = upload_session.chapter
    if not chapter:
        return GenerationResult(
            success=False,
            error="Quiz has no associated chapter.",
            generation_type=GenerationType.QUIZ,
        )

    # ── Lock the most recent quiz for this session to prevent race conditions ──
    # when multiple generate_quiz_task Celery tasks run concurrently.
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
            # Another task already finished this quiz — return it immediately.
            logger.info("[QUIZ] Quiz %s already completed for session %s — returning existing.", quiz.id, upload_session.id)
            return GenerationResult(
                success=True,
                data=quiz,
                generation_type=GenerationType.QUIZ,
            )
        else:
            # Ensure status is processing while we work.
            quiz.status = Quiz.STATUS_PROCESSING
            quiz.save(update_fields=["status"])

    def _fail(error_msg: str) -> GenerationResult:
        """Mark quiz as FAILED and return a failed GenerationResult."""
        if quiz.status in {Quiz.STATUS_PENDING, Quiz.STATUS_PROCESSING}:
            quiz.status = Quiz.STATUS_FAILED
            quiz.error_message = error_msg[:500]
            quiz.save(update_fields=["status", "error_message"])
        return GenerationResult(
            success=False,
            error=error_msg,
            generation_type=GenerationType.QUIZ,
        )

    # OPTIMIZATION: Build quiz context directly from uploaded chunks without re-embedding.
    # RAG semantic search isn't necessary for quiz generation — we want even coverage
    # of the whole document, not just the "most similar" chunks to a query.
    raw_session_chunks = list(
        UploadedChunk.objects.filter(upload_session=upload_session)
        .only("id", "content")
        .order_by("chunk_index")[:60]
    )

    if not raw_session_chunks:
        return _fail("No uploaded chunks available for quiz generation.")

    # Sample chunks evenly for broad coverage (up to 8 chunks)
    num_context_chunks = min(8, max(3, len(raw_session_chunks) // 15))
    if len(raw_session_chunks) <= num_context_chunks:
        selected_chunks = raw_session_chunks
    else:
        step = len(raw_session_chunks) // num_context_chunks
        selected_chunks = [raw_session_chunks[i * step] for i in range(num_context_chunks)]

    context_text = "\n\n".join(f"- {chunk.content}" for chunk in selected_chunks)

    # Light textbook cross-reference (optional — just top 2 chunks from textbook)
    textbook_chunks = list(
        TextbookChunk.objects.filter(chapter=chapter)
        .only("id", "content", "topic")
        .order_by("?")[:2]
    )
    if textbook_chunks:
        context_text += "\n\n[Textbook Reference Context]\n"
        context_text += "\n\n".join(f"- {chunk.content}" for chunk in textbook_chunks)

    cross_ref = "N/A"
    if textbook_chunks:
        topics = [c.topic.title for c in textbook_chunks if c.topic]
        if topics:
            cross_ref = "Related topics: " + ", ".join(topics)

    rag_context = GenerationContext(
        context_text=context_text,
        cross_reference_notes=cross_ref,
    )

    provider = get_generation_provider()

    # Check cache for previously generated quiz
    quiz_cache_key = _cache_key(
        "quiz", chapter.id, _get_content_hash(rag_context.context_text + chapter.title)
    )
    cached_quiz_data = cache.get(quiz_cache_key)
    if cached_quiz_data:
        logger.info("[QUIZ] Cache hit for session %s — skipping AI call.", upload_session.id)
        result = GenerationResult(
            success=True,
            data=cached_quiz_data,
            generation_type=GenerationType.QUIZ,
        )
    else:
        result = provider.generate_mcq(
            rag_context.context_text,
            chapter.title,
            rag_context.cross_reference_notes,
        )
        if result.success and result.data:
            cache.set(quiz_cache_key, result.data, timeout=60 * 60 * 24 * 7)

    if not result.success or not result.data:
        return _fail(result.error or "AI quiz generation returned no data.")

    # ── SUCCESS: populate questions and mark COMPLETED ──
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
    return GenerationResult(
        success=True,
        data=quiz,
        generation_type=GenerationType.QUIZ,
        duration_ms=result.duration_ms,
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
    if cached_recommendation:
        logger.info("[REC] Cache hit for attempt %s — returning cached recommendation.", attempt.id)
        attempt.ai_recommendation = cached_recommendation
        attempt.recommendation_status = QuizAttempt.RECOMMENDATION_COMPLETED
        attempt.save(update_fields=["ai_recommendation", "recommendation_status"])
        return GenerationResult(
            success=True,
            data=cached_recommendation,
            generation_type=GenerationType.RECOMMENDATIONS,
        )

    provider = get_generation_provider()
    result = provider.generate_recommendations(attempt, questions_with_answers)

    if result.success:
        attempt.ai_recommendation = result.data
        attempt.recommendation_status = QuizAttempt.RECOMMENDATION_COMPLETED
        attempt.save(update_fields=["ai_recommendation", "recommendation_status"])
        cache.set(rec_cache_key, result.data, timeout=60 * 60 * 24 * 7)

    return result


def process_upload_session_simple(upload_session_id: int) -> dict:
    """
    Simplified upload processing pipeline with detailed timing.

    Logs every step to quizsense_processing.log with durations.
    """
    from .timing_logger import ProcessingTimer

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

                file.extracted_text = extracted_text
                file.save(update_fields=["extracted_text"])
                timer.detail(f"Extracted {len(extracted_text or '')} chars from {filename}")

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
            upload_session.save(
                update_fields=[
                    "processing_status",
                    "processing_error",
                    "processing_completed_at",
                ]
            )

        logger.info("[SESSION %s] Processing completed in %.1fs", upload_session_id, total_elapsed)
        return results

    except Exception as exc:
        elapsed = time.time() - upload_session.processing_started_at.timestamp()
        logger.exception("[SESSION %s] Upload session processing failed after %.1fs: %s", upload_session_id, elapsed, exc)
        upload_session.processing_status = UploadSession.STATUS_FAILED
        upload_session.processing_error = str(exc)
        upload_session.processing_completed_at = timezone.now()
        upload_session.save(
            update_fields=[
                "processing_status",
                "processing_error",
                "processing_completed_at",
            ]
        )
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