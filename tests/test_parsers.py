"""Automated tests for document parsers, OCR, and template analyzers without live LLM calls."""

from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from app.agents.doc_analyzer import analyze_document
from app.agents.ppt_analyzer import analyze_presentation
from app.models.template_profile import TemplateProfile, ToneProfile
from app.services.parsers import (
    parse_docx,
    parse_file,
    parse_pdf,
    parse_pptx,
    ParsedContent,
)

SAMPLE_DOCX = Path("data/sample_templates/Company_Proposal.docx")
SAMPLE_PPTX = Path("data/sample_templates/Company_Template.pptx")
SAMPLE_PDF = Path("data/sample_kb/case_studies.pdf")
SAMPLE_SCAN = Path("data/sample_scans/scanned_page.png")


@pytest.fixture(autouse=True)
def mock_llm_client():
    """Mock LLMClient generate_json and generate_with_image to avoid network API calls in unit tests."""
    with patch("app.agents.doc_analyzer.get_llm_client") as mock_doc_llm, \
         patch("app.agents.ppt_analyzer.get_llm_client") as mock_ppt_llm, \
         patch("app.services.ocr.get_llm_client") as mock_ocr_llm:

        mock_instance = MagicMock()
        mock_instance.generate_json.return_value = MagicMock(
            formality="formal",
            voice="authoritative",
            person="first_person_plural",
            avg_sentence_length=16.5,
            style_notes=["Professional tone", "Direct statements"],
            typical_openings=["We propose", "Our team"],
            content_summary="Sample enterprise consulting proposal document.",
        )
        mock_instance.generate_with_image.return_value = "STATEMENT OF WORK (SOW) EXCERPT\nSample scanned text output."

        mock_doc_llm.return_value = mock_instance
        mock_ppt_llm.return_value = mock_instance
        mock_ocr_llm.return_value = mock_instance

        yield mock_instance


def test_parse_docx():
    """Test parse_docx extracts blocks, headings, tables from sample DOCX."""
    assert SAMPLE_DOCX.exists(), "Sample DOCX must exist for testing"
    parsed: ParsedContent = parse_docx(SAMPLE_DOCX)

    assert parsed.file_type == "docx"
    assert len(parsed.blocks) > 0
    assert any(b.type == "heading" for b in parsed.blocks)
    assert any(b.type == "table" for b in parsed.blocks)
    assert "NexaWorks" in parsed.raw_text


def test_parse_pptx():
    """Test parse_pptx extracts slides from sample PPTX."""
    assert SAMPLE_PPTX.exists(), "Sample PPTX must exist for testing"
    parsed: ParsedContent = parse_pptx(SAMPLE_PPTX)

    assert parsed.file_type == "pptx"
    assert parsed.page_or_slide_count >= 1
    assert any(b.type == "slide_title" for b in parsed.blocks)


def test_parse_pdf():
    """Test parse_pdf extracts text blocks from sample PDF."""
    assert SAMPLE_PDF.exists(), "Sample PDF must exist for testing"
    parsed: ParsedContent = parse_pdf(SAMPLE_PDF, ocr_scanned=False)

    assert parsed.file_type == "pdf"
    assert len(parsed.blocks) > 0
    assert "NexaWorks" in parsed.raw_text


def test_parse_file_dispatcher():
    """Test parse_file routes correctly based on extension."""
    res_docx = parse_file(SAMPLE_DOCX)
    assert res_docx.file_type == "docx"

    res_pptx = parse_file(SAMPLE_PPTX)
    assert res_pptx.file_type == "pptx"

    res_pdf = parse_file(SAMPLE_PDF, ocr_scanned=False)
    assert res_pdf.file_type == "pdf"


def test_analyze_document():
    """Test analyze_document produces a complete TemplateProfile."""
    assert SAMPLE_DOCX.exists()
    profile: TemplateProfile = analyze_document(SAMPLE_DOCX, file_id=1)

    assert profile.file_id == 1
    assert profile.file_type == "docx"
    assert profile.doc_style is not None
    assert profile.doc_style.body_font is not None
    assert len(profile.doc_style.palette) > 0
    assert profile.tone.formality == "formal"


def test_analyze_presentation():
    """Test analyze_presentation produces a complete TemplateProfile."""
    assert SAMPLE_PPTX.exists()
    profile: TemplateProfile = analyze_presentation(SAMPLE_PPTX, file_id=2)

    assert profile.file_id == 2
    assert profile.file_type == "pptx"
    assert profile.ppt_style is not None
    assert profile.ppt_style.slide_width > 0
    assert len(profile.ppt_style.layouts) > 0
    assert "title" in profile.ppt_style.layout_roles
