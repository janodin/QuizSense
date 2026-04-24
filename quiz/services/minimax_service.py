"""
AI service for QuizSense using MiniMax API.
Responsibilities:
  1. generate_mcq_questions(text, chapter_title) -> list of 10 parsed MCQ dicts
  2. generate_recommendations(attempt, results)  -> plain-text AI advice string
  3. generate_summary(text, chapter_title)      -> study summary
  4. embed_texts(texts)                          -> list of vectors (1536d)
"""

import json
import logging
import re
import requests
from django.conf import settings

# MiniMax Models
CHAT_MODEL = "abab6.5s-chat"
EMBEDDING_MODEL = "embo-01"

logger = logging.getLogger(__name__)

def _make_minimax_request(endpoint, payload):
    url = f"https://api.minimax.chat/v1/{endpoint}"
    headers = {
        "Authorization": f"Bearer {settings.MINIMAX_API_KEY}",
        "Content-Type": "application/json"
    }
    try:
        response = requests.post(url, headers=headers, json=payload, timeout=60)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        logger.error(f"MiniMax API Error: {e}")
        raise ValueError(f"MiniMax API call failed: {e}")

def embed_texts(texts):
    """
    Generate embeddings using MiniMax embo-01 (1536 dimensions).
    """
    if not texts:
        return []
    
    payload = {
        "model": EMBEDDING_MODEL,
        "texts": texts,
        "type": "db" # 'db' for storage, 'query' for search. Service uses it for both for simplicity.
    }
    
    try:
        data = _make_minimax_request("embeddings", payload)
        # MiniMax returns data['vectors']
        return data.get("vectors", [])
    except Exception as exc:
        logger.error(f"MiniMax embedding failed: {exc}")
        raise ValueError(f"MiniMax embedding failed: {exc}")

# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

SUMMARY_PROMPT_TEMPLATE = (
    "You are an expert programming instructor. Based on the retrieved context below, "
    "create a concise study summary to help a student prepare for a quiz.\n\n"
    "FORMAT:\n"
    "- Start each main topic with ## Topic Name\n"
    "- Use bullet points (- ) for key facts and definitions\n"
    "- Keep it focused and under 600 words\n"
    "- Plain text with ## headings and - bullets only\n\n"
    "Chapter: {chapter_title}\n\n"
    "Retrieved Context:\n{text}\n\n"
    "Write the study summary now:"
)

MCQ_PROMPT_TEMPLATE = (
    "You are an expert programming instructor. Based on the retrieved context below, "
    "generate exactly 10 multiple-choice questions to test a student's understanding.\n\n"
    "RULES:\n"
    "- Each question must have exactly 4 answer choices labeled A, B, C, D.\n"
    "- Only ONE choice must be the correct answer.\n"
    "- The correct answer must be factually accurate based on the text.\n"
    "- IMPORTANT: Return ONLY a valid JSON array with NO markdown, NO explanation, NO code fences.\n\n"
    "Chapter: {chapter_title}\n\n"
    "Retrieved Context:\n{text}\n\n"
    "Required JSON format:\n"
    '[{{"question": "...?", "choices": {{"A": "...", "B": "...", "C": "...", "D": "..."}}, '
    '"correct_answer": "A", "topic": "Brief topic"}}]'
)

RECOMMENDATION_PROMPT_TEMPLATE = (
    "You are a helpful programming tutor. A student just completed a quiz on \"{chapter_title}\".\n\n"
    "Results:\n{results_text}\n\n"
    "Score: {score}/{total} ({percentage}%)\n\n"
    "Provide actionable feedback in bullet points using HTML <ul><li> tags.\n"
    "No introduction or conclusion."
)

def _chat_completion(prompt):
    payload = {
        "model": CHAT_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "tokens_to_generate": 2048,
    }
    data = _make_minimax_request("chat/completions", payload)
    # MiniMax V1 response structure
    if "choices" in data:
        return data["choices"][0]["message"]["content"]
    return ""

def generate_summary(text, chapter_title="Programming Fundamentals", cross_reference_notes=""):
    prompt = SUMMARY_PROMPT_TEMPLATE.format(
        chapter_title=chapter_title,
        text=text[:8000]
    )
    return _chat_completion(prompt)

def generate_mcq_questions(text, chapter_title="Programming Fundamentals", cross_reference_notes=""):
    prompt = MCQ_PROMPT_TEMPLATE.format(
        chapter_title=chapter_title,
        text=text[:8000]
    )
    raw = _chat_completion(prompt)
    return _parse_mcq_response(raw)

def _parse_mcq_response(raw):
    cleaned = re.sub(r"```(?:json)?", "", raw).strip().strip("`")
    start = cleaned.find("[")
    end = cleaned.rfind("]")
    if start == -1 or end == -1:
        raise ValueError("MiniMax response did not contain a JSON array.")
    
    questions = json.loads(cleaned[start:end + 1])
    validated = []
    for q in questions:
        if all(k in q for k in ("question", "choices", "correct_answer")):
            validated.append(q)
            if len(validated) == 10: break
    return validated

def generate_recommendations(quiz_attempt, questions_with_answers):
    chapter_title = quiz_attempt.quiz.chapter.title if quiz_attempt.quiz.chapter else "Fundamentals"
    lines = [f"Q{i}: {'Correct' if item['is_correct'] else 'Wrong'} - Topic: {item.get('topic')}" 
             for i, item in enumerate(questions_with_answers, 1)]
    
    prompt = RECOMMENDATION_PROMPT_TEMPLATE.format(
        chapter_title=chapter_title,
        results_text="\n".join(lines),
        score=quiz_attempt.score,
        total=quiz_attempt.total_questions,
        percentage=quiz_attempt.score_percentage()
    )
    return _chat_completion(prompt)
