"""
Test upload processing directly to diagnose issues.
Usage: python manage.py test_upload_processing <upload_session_id>
"""
import logging
import time
from django.core.management.base import BaseCommand
from quiz.services.pipeline_service import process_upload_session_simple

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Test upload processing directly and show detailed output"

    def add_arguments(self, parser):
        parser.add_argument('upload_session_id', type=int, help='Upload session ID to process')

    def handle(self, *args, **options):
        upload_session_id = options['upload_session_id']
        
        self.stdout.write(self.style.NOTICE(f"Testing upload session {upload_session_id}..."))
        self.stdout.write("=" * 60)
        
        start_time = time.time()
        try:
            result = process_upload_session_simple(upload_session_id)
            elapsed = time.time() - start_time
            
            self.stdout.write(self.style.SUCCESS(f"\nProcessing completed in {elapsed:.1f}s"))
            self.stdout.write(f"Summary success: {result['summary'].success if result['summary'] else False}")
            if result['summary'] and not result['summary'].success:
                self.stdout.write(self.style.ERROR(f"Summary error: {result['summary'].error}"))
                
        except Exception as exc:
            elapsed = time.time() - start_time
            self.stdout.write(self.style.ERROR(f"\nProcessing FAILED after {elapsed:.1f}s"))
            self.stdout.write(self.style.ERROR(f"Error: {exc}"))
            import traceback
            self.stdout.write(traceback.format_exc())