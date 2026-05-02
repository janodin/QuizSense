"""
Diag: run the full upload_processing pipeline on the test PDF
and print stage-by-stage timing so we know where it hangs.
"""
import sys, os, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'quizsense.settings')

import django
django.setup()

from quiz.services.file_processor import extract_text_from_pdf
from quiz.services.chunking_service import split_text_into_chunks
from quiz.services.ai_service import embed_texts

PDF_PATH = r"D:\Desktop\Django Projects\QuizSense\test_file\AN INTRODUCTION TO PROGRAMMING AND COMPUTER SCIENCE WITH PYTHON (itpacs_cafiero).pdf"

print("=" * 60)
print("STEP 1: extract_text_from_pdf")
print("=" * 60)
t0 = time.time()
with open(PDF_PATH, 'rb') as f:
    text = extract_text_from_pdf(f)
print(f"  -> {len(text):,} chars in {time.time()-t0:.1f}s")

print()
print("=" * 60)
print("STEP 2: split_text_into_chunks")
print("=" * 60)
t0 = time.time()
chunks = split_text_into_chunks(text)
print(f"  -> {len(chunks):,} chunks in {time.time()-t0:.1f}s")
if chunks:
    print(f"  First chunk ({len(chunks[0])} chars): {repr(chunks[0][:200])}")

print()
print("=" * 60)
print("STEP 3: embed_texts (first 5 chunks only — for diagnosis)")
print("=" * 60)
t0 = time.time()
# Only embed 5 chunks as a diagnostic probe
sample = chunks[:5]
print(f"  Embedding {len(sample)} chunks...")
try:
    embeddings = embed_texts(sample)
    print(f"  -> {len(embeddings)} embeddings in {time.time()-t0:.1f}s")
    print(f"  Embedding dim: {len(embeddings[0]) if embeddings else 'N/A'}")
except Exception as e:
    print(f"  ERROR: {e}")

print()
print("=" * 60)
print("STEP 4: embed_texts (ALL chunks — measures full pipeline time)")
print("=" * 60)
t0 = time.time()
try:
    all_embeddings = embed_texts(chunks)
    print(f"  -> {len(all_embeddings):,} embeddings in {time.time()-t0:.1f}s")
    print(f"  Embedding dim: {len(all_embeddings[0]) if all_embeddings else 'N/A'}")
except Exception as e:
    print(f"  ERROR: {e}")
