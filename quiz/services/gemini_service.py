"""
AI service for QuizSense.
Uses google-genai SDK with Gemini 3.1 Flash Lite.

Responsibilities:
  1. generate_mcq_questions(text, chapter_title) -> list of 10 parsed MCQ dicts
  2. generate_recommendations(attempt, results)  -> plain-text AI advice string
"""

import json
import re
from google import genai
from django.conf import settings


MODEL = "gemini-3.1-flash-lite-preview"


def _get_client():
    return genai.Client(api_key=settings.GEMINI_API_KEY)


# ---------------------------------------------------------------------------
# 1. MCQ Generation
# ---------------------------------------------------------------------------

SUMMARY_PROMPT_TEMPLATE = (
    "You are an expert programming instructor. Based on the retrieved context below, "
    "create a concise study summary to help a student prepare for a quiz.\n\n"
    "FORMAT:\n"
    "- Start each main topic with ## Topic Name\n"
    "- Use bullet points (- ) for key facts and definitions\n"
    "- Keep it focused and under 600 words\n"
    "- Plain text with ## headings and - bullets only\n\n"
    "GROUNDING RULES:\n"
    "- Use only the retrieved context.\n"
    "- If a detail is missing, do not invent it.\n"
    "- Prioritize textbook-backed concepts when available.\n\n"
    "Chapter: {chapter_title}\n\n"
    "Cross-reference Notes:\n{cross_reference_notes}\n\n"
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
    "- Distractors must be plausible but clearly wrong.\n"
    "- Questions must vary in difficulty (recall, understanding, application).\n"
    "- Do NOT number the questions in the question field.\n"
    "- Every question must be grounded in retrieved context.\n"
    "- Prefer textbook-consistent facts if upload and textbook differ.\n"
    "- IMPORTANT: Generate 2-3 questions per major topic to allow for meaningful performance tracking.\n"
    "- Group related questions under the same topic name (e.g., 'Variables', 'Loops', 'Functions').\n"
    "- Use consistent topic names - don't create too many unique topics.\n"
    "- Aim for 3-5 distinct topics total, with multiple questions per topic.\n"
    "- Return ONLY a valid JSON array with NO markdown, NO explanation, NO code fences.\n\n"
    "Chapter: {chapter_title}\n\n"
    "Cross-reference Notes:\n{cross_reference_notes}\n\n"
    "Retrieved Context:\n{text}\n\n"
    "Required JSON format:\n"
    '[{{"question": "...?", "choices": {{"A": "...", "B": "...", "C": "...", "D": "..."}}, '
    '"correct_answer": "A", "topic": "Brief topic (2-5 words)"}}]'
)

RECOMMENDATION_PROMPT_TEMPLATE = (
    "You are a helpful programming tutor. A student just completed a quiz on \"{chapter_title}\".\n\n"
    "Results:\n{results_text}\n\n"
    "Score: {score}/{total} ({percentage}%)\n\n"
    "Provide actionable feedback in bullet points. Focus on:\n"
    "1. Topics where the student struggled (be specific)\n"
    "2. What key concepts to review\n"
    "3. One concrete study recommendation\n\n"
    "FORMAT REQUIREMENTS:\n"
    "- Use HTML bullet points: <ul><li>...</li></ul>\n"
    "- Keep each bullet concise (1-2 sentences max)\n"
    "- Include only critical information\n"
    "- 3-5 bullets total\n"
    "- Use <strong> tags for topic names\n"
    "- No introduction or conclusion - just the bullet list"
)


def generate_summary(text, chapter_title="Fundamentals of Programming", cross_reference_notes=""):
    """
    Generate a study summary from extracted text.
    Returns a markdown-formatted string, or empty string on failure.
    """
    client = _get_client()
    trimmed = text[:8000]
    prompt = SUMMARY_PROMPT_TEMPLATE.format(
        chapter_title=chapter_title,
        text=trimmed,
        cross_reference_notes=cross_reference_notes or "None",
    )
    try:
        response = client.models.generate_content(model=MODEL, contents=prompt)
        return response.text.strip()
    except Exception:
        return ""


def generate_mcq_questions(text, chapter_title="Fundamentals of Programming", cross_reference_notes=""):
    """
    Call ZhipuAI GLM-4-Plus and return a validated list of up to 10 MCQ dicts.
    Each dict: { question, choices: {A,B,C,D}, correct_answer, topic }
    Raises ValueError on API failure or unparseable response.
    """
    client = _get_client()
    trimmed = text[:8000]
    prompt = MCQ_PROMPT_TEMPLATE.format(
        chapter_title=chapter_title,
        text=trimmed,
        cross_reference_notes=cross_reference_notes or "None",
    )

    try:
        response = client.models.generate_content(model=MODEL, contents=prompt)
        raw = response.text.strip()
    except Exception as exc:
        raise ValueError(f"AI API call failed: {exc}")

    return _parse_mcq_response(raw)


def _parse_mcq_response(raw):
    """Strip markdown fences, JSON-decode, validate, return list of MCQ dicts."""
    cleaned = re.sub(r"```(?:json)?", "", raw).strip().strip("`")

    start = cleaned.find("[")
    end = cleaned.rfind("]")
    if start == -1 or end == -1:
        raise ValueError("Gemini response did not contain a JSON array.")

    try:
        questions = json.loads(cleaned[start:end + 1])
    except json.JSONDecodeError as exc:
        raise ValueError(f"JSON parse error: {exc}")

    validated = []
    for q in questions:
        if not isinstance(q, dict):
            continue
        if not all(k in q for k in ("question", "choices", "correct_answer")):
            continue
        choices = q["choices"]
        if not all(k in choices for k in ("A", "B", "C", "D")):
            continue
        correct = str(q["correct_answer"]).strip().upper()
        if correct not in ("A", "B", "C", "D"):
            continue
        validated.append({
            "question": str(q["question"]).strip(),
            "choices": {k: str(choices[k]).strip() for k in ("A", "B", "C", "D")},
            "correct_answer": correct,
            "topic": str(q.get("topic", "General")).strip(),
        })
        if len(validated) == 10:
            break

    if not validated:
        raise ValueError("AI returned no valid questions after validation.")
    return validated


# ---------------------------------------------------------------------------
# 2. Topic Recommendations / Insights
# ---------------------------------------------------------------------------

def generate_recommendations(quiz_attempt, questions_with_answers):
    """
    Return a plain-text AI recommendation string based on quiz results.

    questions_with_answers: list of dicts with keys:
        question_text, correct_answer, selected_answer, is_correct, topic
    """
    client = _get_client()
    chapter_title = (
        quiz_attempt.quiz.chapter.title
        if quiz_attempt.quiz.chapter
        else "Fundamentals of Programming"
    )

    lines = []
    for i, item in enumerate(questions_with_answers, 1):
        if item["is_correct"]:
            status = "Correct"
        else:
            status = (
                f"Wrong (correct: {item['correct_answer']}, "
                f"selected: {item['selected_answer']})"
            )
        lines.append(f"Q{i} [{item.get('topic', 'General')}]: {status}")

    prompt = RECOMMENDATION_PROMPT_TEMPLATE.format(
        chapter_title=chapter_title,
        results_text="\n".join(lines),
        score=quiz_attempt.score,
        total=quiz_attempt.total_questions,
        percentage=quiz_attempt.score_percentage(),
    )

    try:
        response = client.models.generate_content(model=MODEL, contents=prompt)
        return response.text.strip()
    except Exception:
        return (
            f"Could not generate AI recommendations at this time. "
            f"Score: {quiz_attempt.score}/{quiz_attempt.total_questions}."
        )
