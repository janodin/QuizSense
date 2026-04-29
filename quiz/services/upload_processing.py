import gc
import logging
import threading

from django.db import close_old_connections, transaction
from django.utils import timezone

from ..models import UploadSession
from .file_processor import extract_text_from_docx, extract_text_from_pdf
from .minimax_service import generate_summary
from .rag_service import ingest_uploaded_file_chunks

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

    RAM optimization (Hetzner CX22 / 4 GB):
    - Phase 1 (extraction): sequential file-by-file to avoid loading all PDFs at once.
    - Phase 2 (embedding): ingest chunks BEFORE starting the HTTP API call so that
      the embedding model is warm when RAG retrieval is needed later.
      Runs ingestion to completion before calling the API, keeping peak memory
      bounded (no parallel threads competing for RAM at the same time).
    - Explicit gc.collect() after the heavy phases.
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

        # ── Phase 1: Text extraction (sequential, one file at a time) ─────
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

            if extracted_text and extracted_text.strip():
                successful_files.append(uploaded_file)

        if not successful_files:
            raise ValueError("Could not extract text from the uploaded files.")

        # ── Phase 2: Chunk ingestion + embedding (no parallel threads) ───────
        # Run to completion before the HTTP call so the model is warm for retrieval.
        # Keeping this single-threaded avoids loading both the model and the HTTP
        # response handler simultaneously, which is what caused OOM on the CX22.
        for uploaded_file in successful_files:
            ingest_uploaded_file_chunks(uploaded_file)
            gc.collect()

        # ── Phase 3: AI summary (MiniMax API — network I/O, releases GIL) ──
        # Use the combined text of successful files directly to avoid waiting for DB.
        combined_text = "\n\n".join(f.extracted_text for f in successful_files)[:4000]
        chapter_title = upload_session.chapter.title if upload_session.chapter else 'Fundamentals of Programming'
        summary_text = generate_summary(
            combined_text,
            chapter_title,
        )
        upload_session.summary = summary_text

        upload_session.processing_status = UploadSession.STATUS_COMPLETED
        upload_session.processing_completed_at = timezone.now()
        upload_session.save(update_fields=['summary', 'processing_status', 'processing_completed_at'])

        gc.collect()

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
