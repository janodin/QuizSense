"""
AI service for QuizSense using MiniMax API.
- Chat/MCQ: https://api.minimax.io/anthropic/v1/messages  (Bearer token auth)
- Embeddings: Now handled by sentence-transformers locally (see embedding_service.py)
"""

import json
import logging
import re
import requests
from django.conf import settings

# MiniMax M2.7 model name (confirmed working)
CHAT_MODEL = "MiniMax-M2.7"

ANTHROPIC_URL = "https://api.minimax.io/anthropic/v1/messages"

logger = logging.getLogger(__name__)


def _make_request(url, payload, auth_header=None):
    """POST to MiniMax and handle errors. Returns parsed JSON dict."""
    headers = {
        "Authorization": f"Bearer {auth_header or settings.MINIMAX_API_KEY}",
        "Content-Type": "application/json",
    }
    if "anthropic" in url:
        headers["anthropic-version"] = "2023-06-01"
        headers["anthropic-dangerous-direct-browser-access"] = "true"

    response = requests.post(url, headers=headers, json=payload, timeout=60)

    if response.status_code != 200:
        try:
            error_data = response.json()
            msg = error_data.get("error", {}).get("message") \
                or error_data.get("base_resp", {}).get("status_msg") \
                or response.text
        except Exception:
            msg = response.text
        logger.error(f"MiniMax HTTP {response.status_code}: {msg}")
        raise ValueError(f"MiniMax HTTP {response.status_code}: {msg}")

    data = response.json()

    if "error" in data:
        msg = data["error"].get("message", str(data["error"]))
        logger.error(f"MiniMax API Error: {msg}")
        raise ValueError(f"MiniMax API Error: {msg}")

    if "base_resp" in data and data["base_resp"].get("status_code") != 0:
        msg = data["base_resp"].get("status_msg", f"code {data['base_resp']['status_code']}")
        logger.error(f"MiniMax API Error: {msg}")
        raise ValueError(f"MiniMax API Error: {msg}")

    return data


def _chat_single(prompt, max_tokens=1024):
    """Send a single-user-prompt chat request. Returns text string."""
    payload = {
        "model": CHAT_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
    }
    data = _make_request(ANTHROPIC_URL, payload)

    if data.get("type") == "message":
        for block in data.get("content", []):
            if block.get("type") == "text":
                return block["text"]
    return ""


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

SUMMARY_PROMPT_TEMPLATE = (
    "You are an expert programming instructor. Based on the retrieved context below, "
    "create a concise study summary.\n\n"
    "Chapter: {chapter_title}\n\n"
    "Retrieved Context:\n{text}\n\n"
    "Cross-Reference Notes (textbook topic matches):\n{cross_reference_notes}\n\n"
    "Write the study summary now:"
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
        text=text[:4000],
        cross_reference_notes=cross_reference_notes or "N/A",
    )
    return _chat_single(prompt)


def generate_mcq_questions(text, chapter_title="Programming Fundamentals", cross_reference_notes=""):
    prompt = MCQ_PROMPT_TEMPLATE.format(
        chapter_title=chapter_title,
        text=text[:4000],
        cross_reference_notes=cross_reference_notes or "N/A",
    )
    raw = _chat_single(prompt, max_tokens=2048)
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
    prompt = f"Provide 3 feedback points for a student who scored {quiz_attempt.score}/{quiz_attempt.total_questions} on {chapter_title}."
    return _chat_single(prompt)
