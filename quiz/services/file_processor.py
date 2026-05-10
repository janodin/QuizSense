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
import shutil
import subprocess
import tempfile
from pathlib import Path

import docx
import fitz  # PyMuPDF
import PyPDF2

logger = logging.getLogger(__name__)

PAGE_TEXT_MIN_CHARS = 50
AI_VISION_OCR_MAX_PAGES = 100


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


def extract_text_from_doc(file_obj):
    """
    Convert a legacy Word (.doc) file to a temporary PDF, extract text from it,
    then delete the temporary conversion directory.

    Requires LibreOffice/soffice to be installed on the machine running Celery.
    """
    from django.conf import settings

    configured_converter = getattr(settings, "LIBREOFFICE_PATH", "")
    converter = configured_converter or shutil.which("soffice") or shutil.which("libreoffice")
    if not converter:
        logger.warning("Legacy .doc conversion skipped — LibreOffice/soffice not found")
        return ""

    with tempfile.TemporaryDirectory(prefix="quizsense_doc_") as tmpdir:
        tmp_path = Path(tmpdir)
        doc_path = tmp_path / "upload.doc"

        file_obj.seek(0)
        with doc_path.open("wb") as output:
            for chunk in getattr(file_obj, "chunks", lambda: [file_obj.read()])():
                output.write(chunk)

        try:
            subprocess.run(
                [
                    converter,
                    "--headless",
                    "--convert-to",
                    "pdf",
                    "--outdir",
                    str(tmp_path),
                    str(doc_path),
                ],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=120,
            )
        except subprocess.TimeoutExpired:
            logger.warning("Legacy .doc conversion timed out")
            return ""
        except subprocess.CalledProcessError as e:
            logger.warning("Legacy .doc conversion failed: %s", e.stderr.decode(errors="ignore")[:500])
            return ""

        pdf_path = doc_path.with_suffix(".pdf")
        if not pdf_path.exists():
            generated_pdfs = list(tmp_path.glob("*.pdf"))
            if not generated_pdfs:
                logger.warning("Legacy .doc conversion produced no PDF")
                return ""
            pdf_path = generated_pdfs[0]

        with pdf_path.open("rb") as pdf_file:
            return extract_text_from_pdf(pdf_file)


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
            logger.warning("[AI_VISION_OCR] HTTP %d on page %d: %s", response.status_code, page_number, response.text[:500])
            return ""

        data = response.json()
        if "choices" not in data:
            logger.warning("[AI_VISION_OCR] Unexpected response on page %d: %s", page_number, json.dumps(data)[:500])
            return ""

        page_text = data["choices"][0]["message"]["content"].strip()
        if page_text:
            logger.info("[AI_VISION_OCR] Page %d extracted (%d chars)", page_number, len(page_text))
        return page_text
    except Exception as e:
        logger.warning("AI Vision OCR failed on page %d: %s", page_number, e)
    return ""
