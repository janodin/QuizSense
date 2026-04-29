"""
File processing service for QuizSense.
Handles text extraction from PDF and Word files using:
  - PyPDF2 for standard PDFs (file parsing)
  - python-docx for Word documents (file parsing)
  - pytesseract + Pillow + pdf2image for scanned/image-based PDFs (OCR)

RAM optimization for Hetzner CX22 (4GB):
  - OCR page limit: at most OCR_MAX_PAGES pages per PDF to prevent
    pdf2image from allocating unbounded memory when converting large scans.
"""

import io
import logging

import docx
import PyPDF2

logger = logging.getLogger(__name__)

OCR_MAX_PAGES = 10   # safety cap — each page = ~1-2 MB uncompressed in RAM


def extract_text_from_pdf(file_obj):
    """
    Extract text from a PDF file.
    First attempts standard file parsing via PyPDF2.
    If extracted text is too short (likely a scanned PDF),
    falls back to OCR using pytesseract (capped at OCR_MAX_PAGES pages).
    """
    text = _parse_pdf(file_obj)

    # If parsed text is too short, assume it's a scanned PDF and run OCR.
    if len(text.strip()) < 100:
        file_obj.seek(0)
        text = _ocr_pdf(file_obj)

    return text.strip()


def extract_text_from_docx(file_obj):
    """
    Extract text from a Word (.docx) document using python-docx.
    """
    document = docx.Document(file_obj)
    paragraphs = [para.text for para in document.paragraphs if para.text.strip()]
    return "\n".join(paragraphs).strip()


def _parse_pdf(file_obj):
    """
    Standard PDF text extraction via PyPDF2.
    """
    text_parts = []
    try:
        reader = PyPDF2.PdfReader(file_obj)
        for page in reader.pages:
            page_text = page.extract_text()
            if page_text:
                text_parts.append(page_text)
    except Exception as e:
        logger.warning("PyPDF2 extraction failed: %s", e)
    return "\n".join(text_parts)


def _ocr_pdf(file_obj):
    """
    OCR-based text extraction for scanned/image PDFs.

    Memory guard: converts at most OCR_MAX_PAGES pages per call.
    pdf2image loads each page as a full-resolution PIL image, so
    limiting page count is critical on a 4 GB RAM server.

    Requires pdf2image, poppler, and tesseract-ocr on the system.
    """
    text_parts = []
    try:
        from pdf2image import convert_from_bytes
        import pytesseract

        # Read once into memory (already done by caller); pass bytes to converter.
        file_bytes = file_obj.read()

        # Safety cap: only convert the first N pages to avoid OOM.
        # A real scanned textbook of 200+ pages would otherwise allocate
        # 200 × ~2 MB = 400+ MB just for the image buffer.
        images = convert_from_bytes(file_bytes, first_page=1, last_page=OCR_MAX_PAGES)

        for image in images:
            page_text = pytesseract.image_to_string(image)
            if page_text.strip():
                text_parts.append(page_text)
            # Explicitly close the image to release its memory immediately.
            image.close()

        del images
    except ImportError as e:
        logger.warning(
            "OCR skipped — optional dependency missing: %s. "
            "Install pdf2image, pytesseract, poppler-utils, and tesseract-ocr.",
            e,
        )
    except Exception as e:
        logger.warning("OCR processing failed: %s", e)
    return "\n".join(text_parts)
