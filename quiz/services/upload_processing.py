import logging

from django.db import close_old_connections, transaction
from django.utils import timezone

from ..models import UploadSession

logger = logging.getLogger(__name__)


def start_upload_session_processing(upload_session_id):
    from .pipeline_service import queue_upload_session_processing
    queue_upload_session_processing(upload_session_id)


def process_upload_session(upload_session_id):
    from .pipeline_service import process_upload_session_simple
    process_upload_session_simple(upload_session_id)
