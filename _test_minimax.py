"""
MiniMax API live connectivity test.
Run: python _test_minimax.py
"""
import sys, os
sys.path.insert(0, ".")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "quizsense.settings")

import django
django.setup()

from django.conf import settings
from quiz.services.minimax_service import _chat_single, embed_texts, generate_summary, CHAT_MODEL

print(f"API Key configured: {bool(settings.MINIMAX_API_KEY)}")
print(f"API Key prefix: {settings.MINIMAX_API_KEY[:12]}...")
print(f"Chat model in service: {CHAT_MODEL}")

if not settings.MINIMAX_API_KEY:
    print("ERROR: MINIMAX_API_KEY is not set!")
    sys.exit(1)

# --- Test 1: Simple Chat ---
print("")
print("--- Test 1: Simple Chat ---")
try:
    reply = _chat_single("Say 'QuizSense MiniMax is working!' in exactly those words.", max_tokens=100)
    print(f"  Reply: {reply}")
    print("PASS: Chat API working")
except Exception as e:
    print(f"  ERROR: {e}")

# --- Test 2: Summary Generation ---
print("")
print("--- Test 2: Summary Generation ---")
try:
    summary = generate_summary(
        "Python is a high-level programming language. It supports object-oriented programming. "
        "It has dynamic typing and garbage collection. Python is widely used in web development, "
        "data science, and artificial intelligence applications.",
        chapter_title="Python Basics",
        cross_reference_notes=""
    )
    print(f"  Summary (first 150 chars): {summary[:150]}")
    print("PASS: generate_summary working")
except Exception as e:
    print(f"  ERROR: {e}")

# --- Test 3: Embeddings ---
print("")
print("--- Test 3: Embeddings (embo-01) ---")
try:
    vectors = embed_texts(["Hello QuizSense", "Python programming"])
    print(f"  Returned {len(vectors)} vectors")
    if vectors:
        print(f"  Vector 0 dims: {len(vectors[0])}")
        print(f"  Vector 0 first 5: {vectors[0][:5]}")
    print("PASS: Embedding API working")
except Exception as e:
    print(f"  ERROR: {e}")

print("")
print("=== ALL TESTS COMPLETE ===")
