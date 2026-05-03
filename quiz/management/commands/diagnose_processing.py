"""
Diagnostic script to test upload processing pipeline.
Run: python manage.py diagnose_processing <upload_session_id>
"""
import logging
import time
from django.core.management.base import BaseCommand
from quiz.models import UploadSession, UploadedFile, UploadedChunk

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Diagnose upload processing issues step by step"

    def add_arguments(self, parser):
        parser.add_argument('upload_session_id', type=int, help='Upload session ID to diagnose')

    def handle(self, *args, **options):
        upload_session_id = options['upload_session_id']
        
        self.stdout.write(self.style.NOTICE(f"Diagnosing upload session {upload_session_id}"))
        self.stdout.write("=" * 60)
        
        # Step 1: Check upload session exists
        try:
            upload_session = UploadSession.objects.get(id=upload_session_id)
            self.stdout.write(self.style.SUCCESS(f"✓ Upload session found: {upload_session_id}"))
            self.stdout.write(f"  Status: {upload_session.processing_status}")
            self.stdout.write(f"  Chapter: {upload_session.chapter.title if upload_session.chapter else 'None'}")
            self.stdout.write(f"  Created: {upload_session.created_at}")
        except UploadSession.DoesNotExist:
            self.stdout.write(self.style.ERROR(f"✗ Upload session {upload_session_id} not found"))
            return
        
        # Step 2: Check files
        files = list(upload_session.files.all())
        self.stdout.write(f"\n  Files: {len(files)}")
        for f in files:
            self.stdout.write(f"    - {f.file.name} (type: {f.file_type})")
            self.stdout.write(f"      Extracted text: {len(f.extracted_text or '')} chars")
        
        # Step 3: Check chunks
        chunks = list(UploadedChunk.objects.filter(upload_session=upload_session))
        self.stdout.write(f"\n  Chunks: {len(chunks)}")
        if chunks:
            self.stdout.write(f"    First chunk: {len(chunks[0].content)} chars")
        
        # Step 4: Check summary
        self.stdout.write(f"\n  Summary: {len(upload_session.summary)} chars")
        if upload_session.processing_error:
            self.stdout.write(self.style.ERROR(f"  Error: {upload_session.processing_error}"))
        
        # Step 5: Test MiniMax API if no summary
        if not upload_session.summary:
            self.stdout.write("\n" + self.style.NOTICE("Testing MiniMax API..."))
            try:
                from quiz.services.pipeline_service import MiniMaxProvider
                provider = MiniMaxProvider()
                start = time.time()
                result = provider.generate_summary(
                    "This is a test of the MiniMax API. Please summarize: Python is a programming language.",
                    "Test Chapter",
                    "N/A"
                )
                elapsed = time.time() - start
                self.stdout.write(self.style.SUCCESS(f"✓ MiniMax API responded in {elapsed:.1f}s"))
                self.stdout.write(f"  Response preview: {result[:100]}...")
            except Exception as exc:
                self.stdout.write(self.style.ERROR(f"✗ MiniMax API failed: {exc}"))
        
        self.stdout.write("\n" + "=" * 60)
        self.stdout.write("Diagnosis complete.")
        
        if upload_session.processing_status == 'processing':
            self.stdout.write(self.style.WARNING("\nThe session is still in 'processing' status."))
            self.stdout.write("Possible causes:")
            self.stdout.write("  1. Celery worker is not running (try: celery -A quizsense worker --loglevel=info)")
            self.stdout.write("  2. The processing thread crashed silently")
            self.stdout.write("  3. MiniMax API is hanging")
            self.stdout.write("\nTo process manually, run:")
            self.stdout.write(f"  python manage.py test_upload_processing {upload_session_id}")