"""
AI service for QuizSense using Google Gemini + local sentence-transformers.
- Chat/MCQ: Gemini 2.5 Flash via google.genai
- Embeddings: intfloat/e5-small-v2 via sentence-transformers (384 dimensions, local CPU)
"""

import json
import logging
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from django.conf import settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Rate limiting — prevents Gemini API overload when multiple users upload at once
# 4 concurrent calls max; excess callers block waiting for a slot
# ---------------------------------------------------------------------------
_gemini_semaphore = threading.Semaphore(4)
_gemini_lock = threading.Lock()

# ---------------------------------------------------------------------------
# Gemini client (lazy init)
# ---------------------------------------------------------------------------
_gemini_client = None


def _get_gemini_client():
    global _gemini_client
    if _gemini_client is None:
        import google.genai as genai
        with _gemini_lock:
            if _gemini_client is None:
                _gemini_client = genai.Client(api_key=settings.GOOGLE_API_KEY)
    return _gemini_client


# ------------------------------------------------------------------
# Chat (Gemini 2.5 Flash) with automatic retry on 503
# ------------------------------------------------------------------
CHAT_MODEL = "gemini-2.5-flash-lite"

from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception

def _is_503_unavailable(exc):
    """Return True if exc is a google.genai 503 UNAVAILABLE error."""
    if hasattr(exc, '__cause__'):
        # google.genai.errors.ClientError wraps the API error
        cause = exc.__cause__
        if hasattr(cause, 'args') and cause.args:
            args = cause.args[0] if isinstance(cause.args[0], dict) else {}
            if isinstance(args, dict):
                return args.get('status') == 'UNAVAILABLE'
    return False


@retry(
    stop=stop_after_attempt(2),
    wait=wait_exponential(multiplier=1, min=2, max=15),
    retry=retry_if_exception(_is_503_unavailable),
    reraise=True,
)
def _chat_single(prompt, max_tokens=1024):
    """Send a single-user-prompt via Gemini 2.5 Flash. Rate-limited to 4 concurrent calls.
    Retries up to 2 times on 503 UNAVAILABLE with exponential backoff."""
    _gemini_semaphore.acquire()
    try:
        client = _get_gemini_client()
        response = client.models.generate_content(
            model=CHAT_MODEL,
            contents=prompt,
            config={"max_output_tokens": max_tokens},
        )
        return response.text or ""
    finally:
        _gemini_semaphore.release()


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------
SUMMARY_PROMPT_TEMPLATE = (
    "You are an expert programming instructor. Based on the context below, "
    "write a polished study summary in 180-260 words.\n\n"
    "Chapter: {chapter_title}\n\n"
    "Context:\n{text}\n\n"
    "Cross-Reference Notes:\n{cross_reference_notes}\n\n"
    "Use this exact Markdown structure:\n"
    "## Study Summary\n"
    "### Overview\n"
    "Write 2-3 clear sentences explaining the main idea of the material.\n\n"
    "### Key Concepts\n"
    "- List 4-6 important concepts or definitions from the material.\n\n"
    "### Review Focus\n"
    "- List 2-3 items the student should pay attention to before taking the quiz.\n\n"
    "Return only the study summary Markdown now:"
)

MCQ_PROMPT_TEMPLATE = (
    "You are an expert programming instructor. Generate exactly 10 MCQs as a JSON array.\n\n"
    "Chapter: {chapter_title}\n\n"
    "Retrieved Context:\n{text}\n\n"
    "Cross-Reference Notes (textbook topic matches):\n{cross_reference_notes}\n\n"
    'Return ONLY a valid JSON array. Each object: {{"question", "choices":{{"A","B","C","D"}}, "correct_answer", "topic"}}'
)


def generate_summary(text, chapter_title="Programming Fundamentals", cross_reference_notes=""):
    prompt = SUMMARY_PROMPT_TEMPLATE.format(
        chapter_title=chapter_title,
        text=text[:6000],
        cross_reference_notes=cross_reference_notes or "N/A",
    )
    # 200 words ≈ 800 tokens. Cap at 1024 to let the word limit do the work.
    return _chat_single(prompt, max_tokens=1024)


def generate_mcq_questions(text, chapter_title="Programming Fundamentals", cross_reference_notes=""):
    prompt = MCQ_PROMPT_TEMPLATE.format(
        chapter_title=chapter_title,
        text=text[:12000],
        cross_reference_notes=cross_reference_notes or "N/A",
    )
    raw = _chat_single(prompt, max_tokens=4096)
    return _parse_mcq_response(raw)


def _parse_mcq_response(raw):
    cleaned = re.sub(r"```(?:json)?", "", raw).strip().strip("`")
    start = cleaned.find("[")
    end = cleaned.rfind("]")
    if start == -1 or end == -1:
        raise ValueError(f"AI Response parsing failed. Raw starts with: {raw[:50]}")

    try:
        questions = json.loads(cleaned[start:end + 1])
        return questions[:10]
    except Exception as e:
        raise ValueError(f"JSON Parse Error: {e}")


def generate_recommendations(quiz_attempt, questions_with_answers):
    chapter_title = quiz_attempt.quiz.chapter.title if quiz_attempt.quiz.chapter else "Fundamentals"

    # Build detailed context from the actual attempt
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
    return _chat_single(prompt, max_tokens=1024)


# ---------------------------------------------------------------------------
# Embeddings (local sentence-transformers — e5-small-v2, 384 dimensions)
# E5 requires "query: " or "passage: " prefix on input text
# ---------------------------------------------------------------------------
EMBED_MODEL = "intfloat/e5-small-v2"
EMBED_DIMENSIONS = 384  # e5-small-v2 outputs 384-dim vectors

_st_model = None
_st_model_lock = threading.Lock()


def _get_st_model():
    """Lazy-load sentence-transformers model (cached after first call)."""
    global _st_model
    if _st_model is None:
        with _st_model_lock:
            if _st_model is None:
                from sentence_transformers import SentenceTransformer
                _st_model = SentenceTransformer(EMBED_MODEL)
    return _st_model


def embed_texts(texts):
    """
    Generate embeddings using local sentence-transformers (e5-small-v2).
    Returns list of float lists (384-dim, L2-normalized).
    E5 requires "query: " or "passage: " prefix — we auto-prepend "passage: ".
    """
    if not texts:
        return []

    model = _get_st_model()
    prefixed = [f"passage: {t[:8000]}" for t in texts]
    embeddings = model.encode(prefixed, normalize_embeddings=True)
    return embeddings.tolist()
