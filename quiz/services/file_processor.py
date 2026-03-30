"""
File processing service for QuizSense.
Handles text extraction from PDF and Word files using:
  - PyPDF2 for standard PDFs (file parsing)
  - python-docx for Word documents (file parsing)
  - pytesseract + Pillow for scanned/image-based PDFs (OCR)
"""

import PyPDF2
import docx
import pytesseract
from PIL import Image


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
    except Exception:
        pass
    return "\n".join(text_parts)


def _ocr_pdf(file_obj):
    """
    OCR-based text extraction for scanned/image PDFs.
    Converts each page to an image and runs pytesseract on it.
    Requires pdf2image and poppler to be installed on the system.
    Falls back gracefully if pdf2image is not available.
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
    except ImportError:
        # pdf2image not installed — return empty string gracefully
        pass
    except Exception:
        pass
    return "\n".join(text_parts)
