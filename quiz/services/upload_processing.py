import logging
import threading

from django.db import close_old_connections
from django.utils import timezone

from ..models import UploadSession
from .file_processor import extract_text_from_docx, extract_text_from_pdf
from .minimax_service import generate_summary
from .rag_service import ingest_uploaded_file_chunks, retrieve_context_for_session

logger = logging.getLogger(__name__)


def start_upload_session_processing(upload_session_id):
    """Kick off upload processing in a background thread."""
    worker = threading.Thread(
        target=_process_upload_session_thread,
        args=(upload_session_id,),
        daemon=True,
        name=f"upload-session-{upload_session_id}",
    )
    worker.start()


def _process_upload_session_thread(upload_session_id):
    close_old_connections()
    try:
        process_upload_session(upload_session_id)
    finally:
        close_old_connections()


def process_upload_session(upload_session_id):
    """
    Process an upload session outside the request/response cycle.
    This keeps the initial upload POST fast enough to avoid proxy timeouts.
    """
    upload_session = UploadSession.objects.select_related('chapter').get(id=upload_session_id)
    upload_session.processing_status = UploadSession.STATUS_PROCESSING
    upload_session.processing_error = ''
    upload_session.processing_started_at = timezone.now()
    upload_session.processing_completed_at = None
    upload_session.save(update_fields=[
        'processing_status',
        'processing_error',
        'processing_started_at',
        'processing_completed_at',
    ])

    try:
        successful_files = []
        for uploaded_file in upload_session.files.all().order_by('id'):
            filename = uploaded_file.file.name.lower()
            uploaded_file.file.open('rb')
            try:
                if filename.endswith('.pdf'):
                    extracted_text = extract_text_from_pdf(uploaded_file.file)
                elif filename.endswith('.docx'):
                    extracted_text = extract_text_from_docx(uploaded_file.file)
                else:
                    continue
            finally:
                uploaded_file.file.close()

            uploaded_file.extracted_text = extracted_text
            uploaded_file.save(update_fields=['extracted_text'])

            if not extracted_text or not extracted_text.strip():
                continue

            ingest_uploaded_file_chunks(uploaded_file)
            successful_files.append(uploaded_file)

        if not successful_files:
            raise ValueError("Could not extract text from the uploaded files. Please try different files.")

        context_bundle = retrieve_context_for_session(upload_session, mode='summary')
        upload_session.summary = generate_summary(
            context_bundle['context_text'],
            upload_session.chapter.title if upload_session.chapter else 'Fundamentals of Programming',
            cross_reference_notes=context_bundle['cross_reference_notes'],
        )
        upload_session.processing_status = UploadSession.STATUS_COMPLETED
        upload_session.processing_completed_at = timezone.now()
        upload_session.save(update_fields=['summary', 'processing_status', 'processing_completed_at'])
    except Exception as exc:
        logger.exception("Upload session processing failed for session %s", upload_session_id)
        upload_session.processing_status = UploadSession.STATUS_FAILED
        upload_session.processing_error = str(exc)
        upload_session.processing_completed_at = timezone.now()
        upload_session.save(update_fields=[
            'processing_status',
            'processing_error',
            'processing_completed_at',
        ])
