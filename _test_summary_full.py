"""
Full test of summary generation pipeline.
Traces: PDF -> extract -> combined_text -> generate_summary -> output
"""
import sys, os, time

# Setup Django
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'quizsense.settings')

import django
django.setup()

from quiz.services.file_processor import extract_text_from_pdf
from quiz.services.ai_service import generate_summary

PDF_PATH = r"D:\Desktop\Django Projects\QuizSense\test_file\AN INTRODUCTION TO PROGRAMMING AND COMPUTER SCIENCE WITH PYTHON (itpacs_cafiero).pdf"

print("=" * 80)
print("STEP 1: Extract text from PDF")
print("=" * 80)
start = time.time()
text = extract_text_from_pdf(PDF_PATH)
extract_time = time.time() - start
print(f"Extracted {len(text):,} chars in {extract_time:.1f}s")
print(f"First 300 chars: {repr(text[:300])}")
print(f"Last 200 chars: {repr(text[-200:])}")
print()

# Simulate what upload_processing.py does
combined_text = text[:6000]  # matches generate_summary() internal cap
print(f"Input to generate_summary: {len(combined_text):,} chars")
print("STEP 2: Truncate to 12,000 chars (as upload_processing.py does)")
print("=" * 80)
print(f"Input to generate_summary: {len(combined_text):,} chars")
print(f"First 300 chars: {repr(combined_text[:300])}")
print()

print("=" * 80)
print("STEP 3: Generate summary (calling Gemini)")
print("=" * 80)
print("Sending prompt to Gemini 2.5 Flash...")
start = time.time()
summary = generate_summary(combined_text, chapter_title="Introduction to Programming with Python")
gen_time = time.time() - start
print(f"\nGeneration took {gen_time:.1f}s")
print(f"Summary length: {len(summary):,} chars / {len(summary.splitlines()):,} lines")
print()
print("=" * 80)
print("FULL SUMMARY OUTPUT:")
print("=" * 80)
print(summary)
print()
print("=" * 80)
print("END OF SUMMARY")
print("=" * 80)
