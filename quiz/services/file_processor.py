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

PAGE_TEXT_MIN_CHARS = 50
AI_VISION_OCR_MAX_PAGES = 20
AI_VISION_OCR_RENDER_SCALE = 3
AI_VISION_OCR_RETRY_RENDER_SCALE = 4
AI_VISION_OCR_RETRY_MIN_CHARS = 80


def extract_text_from_pdf(file_obj):
    """
    Extract text from a PDF file.
    Processes each page independently: PyMuPDF first, PyPDF2 fallback, then
    AI Vision OCR only for low-text/scanned pages up to the configured cap.
    """
    from django.conf import settings

    page_texts = []
    ocr_pages_used = 0
    ocr_max_pages = getattr(settings, "AI_VISION_OCR_MAX_PAGES", AI_VISION_OCR_MAX_PAGES)

    try:
        file_obj.seek(0)
        file_bytes = file_obj.read()
        doc = fitz.open(stream=file_bytes, filetype="pdf")
    except Exception as e:
        logger.warning("PyMuPDF document open failed: %s", e)
        file_obj.seek(0)
        return _parse_pdf_pypdf2(file_obj).strip()

    pypdf_reader = None
    try:
        pypdf_reader = PyPDF2.PdfReader(io.BytesIO(file_bytes))
    except Exception as e:
        logger.warning("PyPDF2 reader setup failed: %s", e)

    try:
        for page_num in range(len(doc)):
            page = doc[page_num]
            page_text = ""

            try:
                page_text = (page.get_text() or "").strip()
            except Exception as e:
                logger.warning("PyMuPDF extraction failed on page %d: %s", page_num + 1, e)

            if len(page_text) < PAGE_TEXT_MIN_CHARS and pypdf_reader is not None:
                try:
                    fallback_text = (pypdf_reader.pages[page_num].extract_text() or "").strip()
                    if len(fallback_text) > len(page_text):
                        page_text = fallback_text
                except Exception as e:
                    logger.warning("PyPDF2 extraction failed on page %d: %s", page_num + 1, e)

            if len(page_text) < PAGE_TEXT_MIN_CHARS and ocr_pages_used < ocr_max_pages:
                ocr_text = _ocr_pdf_page_ai_vision(page, page_num + 1)
                ocr_pages_used += 1
                if len(ocr_text.strip()) > len(page_text):
                    page_text = ocr_text.strip()
            elif len(page_text) < PAGE_TEXT_MIN_CHARS and ocr_pages_used >= ocr_max_pages:
                logger.info(
                    "[AI_VISION_OCR] Page %d skipped because OCR cap reached (%d pages)",
                    page_num + 1,
                    ocr_max_pages,
                )

            if page_text:
                page_texts.append(f"--- Page {page_num + 1} ---\n{page_text}")
    finally:
        doc.close()

    return "\n\n".join(page_texts).strip()


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


def _render_page_base64(page, scale):
    mat = fitz.Matrix(scale, scale)
    pix = page.get_pixmap(matrix=mat, alpha=False)
    try:
        img_bytes = pix.tobytes("png")
        return base64.b64encode(img_bytes).decode("utf-8")
    finally:
        pix = None


def _ocr_pdf_page_ai_vision(page, page_number):
    """
    Cloud-based OCR using AI Vision API for a single rendered PDF page.
    """
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

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        prompt = (
            "You are a precise OCR engine. Transcribe every visible word from this page. "
            "Preserve line breaks where possible. Do not summarize. Do not explain. "
            "If the page contains diagrams or code, include all visible labels and code text. "
            "Return only the extracted text. If only a few words are visible, return those words."
        )

        def _request_ocr(scale):
            img_b64 = _render_page_base64(page, scale)
            payload = {
                "model": model,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/png;base64,{img_b64}"
                                },
                            },
                        ],
                    }
                ],
                "max_tokens": 8192,
                "temperature": 0.0,
            }
            response = requests.post(url, headers=headers, json=payload, timeout=90)
            if response.status_code != 200:
                logger.warning(
                    "[AI_VISION_OCR] HTTP %d on page %d at scale %s: %s",
                    response.status_code,
                    page_number,
                    scale,
                    response.text[:500],
                )
                return ""

            data = response.json()
            if "choices" not in data:
                logger.warning(
                    "[AI_VISION_OCR] Unexpected response on page %d at scale %s: %s",
                    page_number,
                    scale,
                    json.dumps(data)[:500],
                )
                return ""
            return data["choices"][0]["message"]["content"].strip()

        render_scale = getattr(settings, "AI_VISION_OCR_RENDER_SCALE", AI_VISION_OCR_RENDER_SCALE)
        retry_scale = getattr(settings, "AI_VISION_OCR_RETRY_RENDER_SCALE", AI_VISION_OCR_RETRY_RENDER_SCALE)
        retry_min_chars = getattr(settings, "AI_VISION_OCR_RETRY_MIN_CHARS", AI_VISION_OCR_RETRY_MIN_CHARS)

        page_text = _request_ocr(render_scale)
        if len(page_text) < retry_min_chars and retry_scale > render_scale:
            logger.info(
                "[AI_VISION_OCR] Page %d short OCR output (%d chars); retrying at scale %s",
                page_number,
                len(page_text),
                retry_scale,
            )
            retry_text = _request_ocr(retry_scale)
            if len(retry_text) > len(page_text):
                page_text = retry_text

        if page_text:
            logger.info("[AI_VISION_OCR] Page %d extracted (%d chars)", page_number, len(page_text))
            if len(page_text) < retry_min_chars:
                logger.info("[AI_VISION_OCR] Page %d short text preview: %r", page_number, page_text[:120])
        return page_text
    except Exception as e:
        logger.warning("AI Vision OCR failed on page %d: %s", page_number, e)
    return ""
