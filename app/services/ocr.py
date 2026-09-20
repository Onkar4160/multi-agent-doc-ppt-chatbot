"""OCR engine for image and scanned PDF text extraction using Gemini Vision & PyMuPDF."""

from __future__ import annotations

import logging
from pathlib import Path
import pymupdf

from app.llm.client import get_llm_client

logger = logging.getLogger(__name__)

OCR_PROMPT = (
    "Extract all legible text from this document image accurately. "
    "Maintain reading order and table structure where possible. "
    "Return only the extracted text without introductory or concluding remarks."
)


def ocr_image(image_bytes: bytes, mime_type: str = "image/png") -> str:
    """OCR an image using Gemini vision LLM wrapper."""
    client = get_llm_client()
    try:
        extracted = client.generate_with_image(
            prompt=OCR_PROMPT,
            image_bytes=image_bytes,
            mime_type=mime_type,
        )
        return extracted.strip()
    except Exception as exc:
        logger.warning("OCR image extraction failed: %s", exc)
        return ""


def ocr_scanned_pdf(file_path: str | Path, max_pages: int = 8, dpi: int = 150) -> str:
    """Render scanned PDF pages to PNG at target DPI and perform OCR (capped at max_pages)."""
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"PDF file not found: {file_path}")

    doc = pymupdf.open(path)
    extracted_pages: list[str] = []
    num_pages = min(len(doc), max_pages)

    logger.info("Performing OCR on %d page(s) of %s", num_pages, path.name)

    for idx in range(num_pages):
        page = doc[idx]
        pix = page.get_pixmap(dpi=dpi)
        png_bytes = pix.tobytes("png")
        page_text = ocr_image(png_bytes, mime_type="image/png")
        if page_text:
            extracted_pages.append(f"--- Page {idx + 1} ---\n{page_text}")

    doc.close()
    return "\n\n".join(extracted_pages)
