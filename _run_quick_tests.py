"""Quick unit tests — no Django DB needed. Run: python _run_quick_tests.py"""
import sys, os, re

print("=== Chunking Tests ===")

from quiz.services.chunking_service import split_text_into_chunks

chunks = split_text_into_chunks("Hello world.", 500, 100)
assert len(chunks) == 1, f"Expected 1, got {len(chunks)}"
print("PASS: short text -> 1 chunk")

words = " ".join([f"word{i}" for i in range(500)])
chunks = split_text_into_chunks(words, 500, 100)
assert len(chunks) == 1
print("PASS: 500 words -> 1 chunk")

# step = 500 - 100 = 400
# 1000 words: [0:500], [400:900], [800:1000] = 3 chunks
words = " ".join([f"word{i}" for i in range(1000)])
chunks = split_text_into_chunks(words, 500, 100)
assert len(chunks) == 3, f"Expected 3, got {len(chunks)}"
print("PASS: 1000 words -> 3 chunks (step=400, 100-word overlap)")

# Verify exact 100-word overlap
overlap = set(chunks[0].split()) & set(chunks[1].split())
assert len(overlap) == 100, f"Expected 100-word overlap, got {len(overlap)}"
print("PASS: 100-word overlap verified")

assert split_text_into_chunks("") == []
assert split_text_into_chunks(None) == []
print("PASS: empty text -> []")

chunks = split_text_into_chunks("Hello  \r\n\r\n   world  \t  is  \r\n  here.", 500, 100)
assert "\n" not in chunks[0] and "\t" not in chunks[0]
print("PASS: whitespace normalized")

print()
print("=== MCQ Parser Tests ===")

from quiz.services.minimax_service import _parse_mcq_response

raw = '[{"question":"What is a variable?","choices":{"A":"A storage location","B":"A function","C":"A loop","D":"A class"},"correct_answer":"A","topic":"Variables"}]'
result = _parse_mcq_response(raw)
assert len(result) == 1
assert result[0]["question"] == "What is a variable?"
assert result[0]["correct_answer"] == "A"
print("PASS: valid JSON parsed correctly")

raw = '```json\r\n[{"question":"Q1?","choices":{"A":"a","B":"b","C":"c","D":"d"},"correct_answer":"B","topic":"T1"}]\r\n```'
result = _parse_mcq_response(raw)
assert len(result) == 1
assert result[0]["correct_answer"] == "B"
print("PASS: JSON with code fences parsed")

# Truncated within the JSON object (missing closing brace) — parser can't recover
r2 = '[{"question":"Q1?","choices":{"A":"a","B":"b","C":"c","D":"d"},"correct_answer":"C","topic":"T1'
try:
    _parse_mcq_response(r2)
    assert False, "Should have raised ValueError"
except ValueError:
    print("PASS: deeply truncated JSON raises ValueError")

try:
    _parse_mcq_response("not json at all")
    assert False, "Should have raised ValueError"
except ValueError:
    print("PASS: malformed JSON raises ValueError")

print()
print("=== Topic Normalization Tests ===")

def _normalize(title):
    return re.sub(r'[^a-z0-9\s]', '', title.lower()).strip()

assert _normalize("Variables and Data Types") == "variables and data types"
assert _normalize("  Variables  ") == "variables"
assert _normalize("Variables") == _normalize("variables")
print("PASS: topic normalization works")

print()
print("ALL UNIT TESTS PASSED")
