"""
Full end-to-end pipeline diagnostic.
Tests: upload_processing.process_upload_session on a real UploadSession.
Prints every stage with timing so we know exactly where it hangs.
"""
import sys, os, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'quizsense.settings')

import django
django.setup()

from django.contrib.auth import get_user_model
from quiz.models import UploadSession, UploadedFile, Chapter
from quiz.services.upload_processing import process_upload_session

PDF_PATH = r"D:\Desktop\Django Projects\QuizSense\test_file\AN INTRODUCTION TO PROGRAMMING AND COMPUTER SCIENCE WITH PYTHON (itpacs_cafiero).pdf"

User = get_user_model()

# Find or create a test user
user, _ = User.objects.get_or_create(
    username="diag_user",
    defaults={"email": "diag@test.com"},
)

# Find or create a test chapter
chapter, _ = Chapter.objects.get_or_create(
    number=99,
    defaults={"title": "Diagnostic Chapter"},
)

# Create a fresh UploadSession
session = UploadSession.objects.create(
    chapter=chapter,
    session_key="diagnostic_session",
)

# Attach the PDF file
with open(PDF_PATH, 'rb') as f:
    from django.core.files import File
    uf = UploadedFile.objects.create(
        upload_session=session,
        chapter=chapter,
        file=File(f, name="test_pdf.pdf"),
        file_type="pdf",
    )

print(f"UploadSession {session.id} created. Starting processing...")
print(f"  File: {PDF_PATH}")

t0 = time.time()
try:
    process_upload_session(session.id)
    elapsed = time.time() - t0
    session.refresh_from_db()
    print(f"\n=== RESULT ===")
    print(f"  Status: {session.processing_status}")
    print(f"  Stage: {session.processing_stage}")
    print(f"  Summary length: {len(session.summary) if session.summary else 0} chars")
    print(f"  Error: {session.processing_error or 'None'}")
    print(f"  Total time: {elapsed:.1f}s")
    print(f"\nSummary preview:\n{session.summary[:500] if session.summary else 'NONE'}")
finally:
    # Cleanup
    uf.delete()
    session.delete()
    print("\nCleanup done.")
