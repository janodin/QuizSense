"""
AI service for QuizSense using MiniMax V2 API (OpenAI Compatible).
"""

import json
import logging
import re
import requests
from django.conf import settings

# MiniMax V2 Models (OpenAI Compatible)
CHAT_MODEL = "abab6.5s-chat"
EMBEDDING_MODEL = "embo-01" # Or 'extraction-601' for V2

logger = logging.getLogger(__name__)

def _make_minimax_v2_request(endpoint, payload):
    # V2 uses the /v2/ prefix and OpenAI compatible structure
    url = f"https://api.minimax.chat/v1/{endpoint}"
    
    headers = {
        "Authorization": f"Bearer {settings.MINIMAX_API_KEY}",
        "Content-Type": "application/json"
    }
    
    # For V2/OpenAI style, some endpoints might differ. 
    # Chat: /v1/chat/completions
    # Embed: /v1/embeddings
    
    response = requests.post(url, headers=headers, json=payload, timeout=60)
    
    if response.status_code != 200:
        try:
            error_data = response.json()
            msg = error_data.get('error', {}).get('message', response.text)
            error_msg = f"MiniMax API Error: {msg}"
        except:
            error_msg = f"MiniMax HTTP {response.status_code}: {response.text}"
        
        logger.error(error_msg)
        raise ValueError(error_msg)
    
    data = response.json()

    # Check for MiniMax specific 'base_resp' error even on 200 OK
    if "base_resp" in data and data["base_resp"].get("status_code") != 0:
        error_msg = f"MiniMax Logic Error {data['base_resp'].get('status_code')}: {data['base_resp'].get('status_msg')}"
        logger.error(error_msg)
        raise ValueError(error_msg)
    
    return data

def embed_texts(texts):
    """
    Generate embeddings using MiniMax embo-01 (1536 dimensions).
    """
    if not texts:
        return []
    
    all_vectors = []
    batch_size = 10 
    
    for i in range(0, len(texts), batch_size):
        batch = texts[i:i + batch_size]
        payload = {
            "model": EMBEDDING_MODEL,
            "input": batch, # V2 uses 'input' instead of 'texts'
        }
        # Note: If this still fails with 2049, your key might require the 'base_resp' check removed 
        # or a different base URL. But 'sk-cp-' usually works with standard OpenAI-style V1 paths.
        data = _make_minimax_v2_request("embeddings", payload)
        
        # OpenAI style returns data['data'][i]['embedding']
        if "data" in data:
            vectors = [item["embedding"] for item in data["data"]]
            all_vectors.extend(vectors)
        elif "vectors" in data: # Fallback for some MiniMax configurations
            all_vectors.extend(data["vectors"])
    
    return all_vectors

# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

SUMMARY_PROMPT_TEMPLATE = (
    "You are an expert programming instructor. Based on the retrieved context below, "
    "create a concise study summary.\n\n"
    "Chapter: {chapter_title}\n\n"
    "Retrieved Context:\n{text}\n\n"
    "Write the study summary now:"
)

MCQ_PROMPT_TEMPLATE = (
    "You are an expert programming instructor. Generate exactly 10 MCQs as a JSON array.\n\n"
    "Chapter: {chapter_title}\n\n"
    "Retrieved Context:\n{text}\n\n"
    "Return ONLY a valid JSON array. Each object: {\"question\", \"choices\":{\"A\",\"B\",\"C\",\"D\"}, \"correct_answer\", \"topic\"}"
)

def _chat_completion(prompt):
    payload = {
        "model": CHAT_MODEL,
        "messages": [{"role": "user", "content": prompt}],
    }
    data = _make_minimax_v2_request("chat/completions", payload)
    if "choices" in data:
        return data["choices"][0]["message"]["content"]
    return ""

def generate_summary(text, chapter_title="Programming Fundamentals", cross_reference_notes=""):
    prompt = SUMMARY_PROMPT_TEMPLATE.format(chapter_title=chapter_title, text=text[:6000])
    return _chat_completion(prompt)

def generate_mcq_questions(text, chapter_title="Programming Fundamentals", cross_reference_notes=""):
    prompt = MCQ_PROMPT_TEMPLATE.format(chapter_title=chapter_title, text=text[:6000])
    raw = _chat_completion(prompt)
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
    return _chat_completion(prompt)
