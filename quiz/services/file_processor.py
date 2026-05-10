"""
File processing service for QuizSense.
Handles text extraction from PDF and Word files using:
  - PyMuPDF (fitz) for standard PDFs (primary, fastest)
  - PyPDF2 as fallback text extraction
  - AI Vision API for cloud-based OCR (scanned PDFs)
  - python-docx for Word documents
"""

import base64
import io
import logging

import docx
import fitz  # PyMuPDF
import PyPDF2

logger = logging.getLogger(__name__)

OCR_MAX_PAGES = 10


def extract_text_from_pdf(file_obj):
    """
    Extract text from a PDF file.
    Tries PyMuPDF first, then PyPDF2, then AI Vision OCR for scanned PDFs.
    """
    text = _parse_pdf_pymupdf(file_obj)

    if len(text.strip()) < 100:
        file_obj.seek(0)
        text_pypdf = _parse_pdf_pypdf2(file_obj)
        if len(text_pypdf.strip()) > len(text.strip()):
            text = text_pypdf

    if len(text.strip()) < 100:
        file_obj.seek(0)
        text_ocr = _ocr_pdf_ai_vision(file_obj)
        if len(text_ocr.strip()) > len(text.strip()):
            text = text_ocr

    return text.strip()


def extract_text_from_docx(file_obj):
    """
    Extract text from a Word (.docx) document using python-docx.
    """
    document = docx.Document(file_obj)
    paragraphs = [para.text for para in document.paragraphs if para.text.strip()]
    return "\n".join(paragraphs).strip()


def _parse_pdf_pymupdf(file_obj):
    """Fast PDF text extraction via PyMuPDF (fitz)."""
    text_parts = []
    try:
        file_obj.seek(0)
        doc = fitz.open(stream=file_obj.read(), filetype="pdf")
        for page in doc:
            page_text = page.get_text()
            if page_text:
                text_parts.append(page_text)
        doc.close()
    except Exception as e:
        logger.warning("PyMuPDF extraction failed: %s", e)
    return "\n".join(text_parts)


def _parse_pdf_pypdf2(file_obj):
    """Fallback PDF text extraction via PyPDF2."""
    text_parts = []
    try:
        file_obj.seek(0)
        reader = PyPDF2.PdfReader(file_obj)
        for page in reader.pages:
            page_text = page.extract_text()
            if page_text:
                text_parts.append(page_text)
    except Exception as e:
        logger.warning("PyPDF2 extraction failed: %s", e)
    return "\n".join(text_parts)


def _ocr_pdf_ai_vision(file_obj):
    """
    Cloud-based OCR using AI Vision API.
    Renders PDF pages to images via PyMuPDF, then sends to AI provider for text extraction.
    No local OCR installation required.
    """
    text_parts = []
    try:
        import json
        import requests
        from django.conf import settings

        api_key = getattr(settings, 'AI_PROVIDER_API_KEY', '')
        if not api_key:
            logger.warning("AI Vision OCR skipped — AI_PROVIDER_API_KEY not set")
            return ""

        url = "https://api.deepinfra.com/v1/openai/chat/completions"
        model = "meta-llama/Llama-3.2-90B-Vision-Instruct"

        file_obj.seek(0)
        doc = fitz.open(stream=file_obj.read(), filetype="pdf")
        total_pages = min(OCR_MAX_PAGES, len(doc))

        for page_num in range(total_pages):
            page = doc[page_num]
            mat = fitz.Matrix(2, 2)
            pix = page.get_pixmap(matrix=mat)
            img_bytes = pix.tobytes("png")
            img_b64 = base64.b64encode(img_bytes).decode("utf-8")

            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            }
            payload = {
                "model": model,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": "Extract all text from this image. Return only the extracted text with no additional commentary.",
                            },
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/png;base64,{img_b64}"
                                },
                            },
                        ],
                    }
                ],
                "max_tokens": 4096,
                "temperature": 0.0,
            }

            response = requests.post(url, headers=headers, json=payload, timeout=60)
            if response.status_code != 200:
                logger.warning("[AI_VISION_OCR] HTTP %d: %s", response.status_code, response.text[:500])
                continue
            data = response.json()
            if "choices" not in data:
                logger.warning("[AI_VISION_OCR] Unexpected response: %s", json.dumps(data)[:500])
                continue
            page_text = data["choices"][0]["message"]["content"].strip()

            if page_text:
                text_parts.append(page_text)
                logger.info("[AI_VISION_OCR] Page %d extracted (%d chars)", page_num + 1, len(page_text))

            pix = None

        doc.close()
    except Exception as e:
        logger.warning("AI Vision OCR processing failed: %s", e)
    return "\n".join(text_parts)
