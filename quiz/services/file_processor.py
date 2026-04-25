"""
File processing service for QuizSense.
Handles text extraction from PDF and Word files using:
  - PyPDF2 for standard PDFs (file parsing)
  - python-docx for Word documents (file parsing)
  - pytesseract + Pillow + pdf2image for scanned/image-based PDFs (OCR)
"""

import logging

import docx
import pytesseract
import PyPDF2
from PIL import Image

logger = logging.getLogger(__name__)


def extract_text_from_pdf(file_obj):
    """
    Extract text from a PDF file.
    First attempts standard file parsing via PyPDF2.
    If extracted text is too short (likely a scanned PDF),
    falls back to OCR using pytesseract.
    """
    text = _parse_pdf(file_obj)

    # If parsed text is too short, assume it's a scanned PDF and run OCR
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
        logger.warning(f"PyPDF2 extraction failed: {e}")
    return "\n".join(text_parts)


def _ocr_pdf(file_obj):
    """
    OCR-based text extraction for scanned/image PDFs.
    Converts each page to an image and runs pytesseract on it.
    Requires pdf2image, poppler, and tesseract-ocr to be installed on the system.
    Logs failures instead of silently ignoring them.
    """
    text_parts = []
    try:
        from pdf2image import convert_from_bytes
        file_bytes = file_obj.read()
        images = convert_from_bytes(file_bytes)
        for image in images:
            page_text = pytesseract.image_to_string(image)
            if page_text.strip():
                text_parts.append(page_text)
    except ImportError as e:
        logger.warning(
            "OCR skipped — pdf2image is not installed. "
            "Run: pip install pdf2image. Also ensure poppler and tesseract-ocr are installed system-wide."
        )
    except Exception as e:
        logger.warning(f"OCR processing failed: {e}")
    return "\n".join(text_parts)
