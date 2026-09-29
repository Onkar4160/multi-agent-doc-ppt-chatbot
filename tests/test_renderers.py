"""Automated unit tests for DOCX and PPTX renderers asserting quality rules, font minimums, and placeholder cleanup."""

from pathlib import Path
import docx
import pptx
import pytest

from app.models.deck_model import BulletItem, DeckModel, SlideModel
from app.models.document_model import (
    BulletsBlock,
    DocumentModel,
    ParagraphBlock,
    Section,
    TableBlock,
)
from app.agents.doc_analyzer import analyze_document
from app.agents.ppt_analyzer import analyze_presentation
from app.services.docx_renderer import render_docx
from app.services.pptx_renderer import render_pptx

DOCX_TEMPLATE = Path("data/sample_templates/Company_Proposal.docx")
PPTX_TEMPLATE = Path("data/sample_templates/Green Cream Simple Aesthetic Watercolor Presentation.pptx")


def test_docx_renderer_quality_and_layout(tmp_path: Path):
    """Test rendering DocumentModel to DOCX file verifying keep_with_next and styles."""
    assert DOCX_TEMPLATE.exists()
    profile = analyze_document(DOCX_TEMPLATE)

    doc_model = DocumentModel(
        title="Generative AI Transformation Proposal",
        subtitle="Technical & Commercial Engagement Architecture",
        client_name="Apex Global Enterprise",
        date="September 2026",
        sections=[
            Section(
                heading="1. Introduction & Context",
                level=1,
                blocks=[
                    ParagraphBlock(text="NexaWorks AI Solutions provides enterprise generative AI engineering.", source_ids=[1]),
                    BulletsBlock(items=["Multi-agent workflow orchestration", "Pinecone hybrid vector search"], source_ids=[1, 2]),
                    TableBlock(headers=["Phase", "Cost"], rows=[["Phase 1", "INR 480k"], ["Phase 2", "INR 720k"]], source_ids=[3]),
                ]
            )
        ]
    )

    sources_map = {
        1: {"title": "Source One", "url": "https://source1.com", "snippet": "Snippet 1"},
        2: {"title": "Source Two", "url": "https://source2.com", "snippet": "Snippet 2"},
        3: {"title": "Source Three", "url": "https://source3.com", "snippet": "Snippet 3"},
    }

    out_file = tmp_path / "test_rendered.docx"
    render_docx(doc_model, DOCX_TEMPLATE, profile, out_file, sources_map=sources_map)

    assert out_file.exists()
    assert out_file.stat().st_size > 0

    # Reopen rendered DOCX and verify headings and keep_with_next
    doc = docx.Document(out_file)
    headings = [p for p in doc.paragraphs if p.style and p.style.name.startswith("Heading")]
    assert len(headings) >= 2  # Section heading + Sources heading

    for h in headings:
        assert h.paragraph_format.keep_with_next is True

    # Assert Sources section exists
    assert any("Sources & References" in p.text for p in doc.paragraphs)


def test_pptx_renderer_quality_and_font_minimums(tmp_path: Path):
    """Test rendering DeckModel to PPTX file verifying font minimums, placeholder cleanup, and layout rotation."""
    assert PPTX_TEMPLATE.exists()
    profile = analyze_presentation(PPTX_TEMPLATE)

    slides_list = [
        SlideModel(role="title", title="Generative AI Transformation Strategy", subtitle="NexaWorks AI Solutions for Indian Enterprise", source_ids=[1]),
        SlideModel(role="section_header", title="Executive Context & Industry Trends", subtitle="Rapid adoption of agentic workflows across Indian enterprise sectors", source_ids=[1]),
        SlideModel(role="title_content", title="Enterprise Market Trends 2026", bullets=[
            BulletItem(text="72% of mid-size Indian enterprises plan agentic workflow adoption by Q4 2026.", level=0, source_ids=[1]),
            BulletItem(text="Manual proposal creation consumes 4.2 hours per client pitch deck.", level=0, source_ids=[1]),
        ]),
        SlideModel(role="two_content", title="Manual Workflows vs NexaWorks AI", left=[
            BulletItem(text="Manual 4.2 hours proposal SLA", level=0, source_ids=[1]),
        ], right=[
            BulletItem(text="Automated 15-minute generation SLA", level=0, source_ids=[1]),
        ]),
        SlideModel(role="section_header", title="NexaWorks Architecture & Capabilities", subtitle="Turnkey multi-agent automation using LangGraph and Pinecone"),
        SlideModel(role="title_content", title="Multi-Agent System Capabilities", bullets=[
            BulletItem(text="LangGraph supervisor agent orchestrates specialized worker sub-agents.", level=0),
            BulletItem(text="Pinecone serverless hybrid vector search achieves 99.4% retrieval precision.", level=0),
        ]),
        SlideModel(role="title_content", title="Document & PPT Template Engineering", bullets=[
            BulletItem(text="Extracts font maps, color palettes, and margin tokens from uploaded templates.", level=0),
        ]),
        SlideModel(role="section_header", title="Proven Enterprise Case Studies", subtitle="Demonstrated SLA reductions across Finance, Healthcare, and Supply Chain"),
        SlideModel(role="title_content", title="Financial Services & Healthcare Impact", bullets=[
            BulletItem(text="80% reduction in turnaround time for investment memo processing.", level=0),
        ]),
        SlideModel(role="title_content", title="Supply Chain RFP Automation Impact", bullets=[
            BulletItem(text="Proposal turnaround time reduced from 7 days to under 24 hours.", level=0),
        ]),
        SlideModel(role="two_content", title="Flexible Engagement Structures", left=[
            BulletItem(text="Turnkey Fixed-Price Sprints", level=0),
        ], right=[
            BulletItem(text="Time & Materials Rate Card", level=0),
        ]),
        SlideModel(role="title_only", title="Initiate Enterprise Transformation", subtitle="Contact NexaWorks AI Solutions"),
    ]

    deck_model = DeckModel(title="12 Slide Quality Test Deck", slides=slides_list)

    sources_map = {
        1: {"title": "Source One", "url": "https://source1.com", "snippet": "Snippet 1"},
    }

    out_file = tmp_path / "test_rendered.pptx"
    render_pptx(deck_model, PPTX_TEMPLATE, profile, out_file, sources_map=sources_map)

    assert out_file.exists()
    assert out_file.stat().st_size > 0

    prs = pptx.Presentation(out_file)

    # 1. Assert slide count (12 deck slides + 1 Sources slide = 13 slides total)
    assert len(prs.slides) == 13

    # 2. Assert layout choices used across 12 slides are NOT all the same
    used_layout_names = {s.slide_layout.name for s in prs.slides}
    assert len(used_layout_names) > 1, f"Layouts used should be varied, found only: {used_layout_names}"

    # 3. Assert no empty text placeholders left on any slide
    for slide_idx, slide in enumerate(prs.slides, start=1):
        for shape in slide.placeholders:
            if shape.has_text_frame:
                txt = shape.text_frame.text.strip()
                assert len(txt) > 0, f"Slide {slide_idx} has empty text placeholder '{shape.name}'"

    # 4. Assert font sizes are >= minimums
    cover_slide = prs.slides[0]
    cover_title_p = cover_slide.shapes.title.text_frame.paragraphs[0]
    assert cover_title_p.font.size.pt >= 28.0, f"Cover title font size {cover_title_p.font.size.pt} is below min 28pt"

    for slide_idx, slide in enumerate(list(prs.slides)[1:], start=2):
        for shape in slide.shapes:
            if shape.has_text_frame:
                for p in shape.text_frame.paragraphs:
                    if p.font.size:
                        assert p.font.size.pt >= 14.0, f"Slide {slide_idx} paragraph font size {p.font.size.pt} is below min 14pt"


def test_strip_markdown_helper():
    """Test regex helper strips stray markdown markers like **, __, and leading #'s."""
    from app.services.docx_renderer import strip_markdown as docx_strip_md
    from app.services.pptx_renderer import strip_markdown as pptx_strip_md

    sample = "### **Executive Summary** with __important__ metrics"
    res = docx_strip_md(sample)
    assert "**" not in res
    assert "__" not in res
    assert "#" not in res
    assert res == "Executive Summary with important metrics"

    assert pptx_strip_md("**Bold Title**") == "Bold Title"
    assert pptx_strip_md("__Underlined Subtitle__") == "Underlined Subtitle"


def test_empty_block_and_slide_skipped(tmp_path: Path):
    """Test that zero-content blocks, sections, and slides are skipped rather than rendered blank."""
    # 1. DOCX empty section & block skipping
    doc_model = DocumentModel(
        title="Test Doc",
        sections=[
            Section(
                heading="1. Valid Section",
                level=1,
                blocks=[
                    ParagraphBlock(text="Valid text block.", source_ids=[]),
                    ParagraphBlock(text="   ", source_ids=[]),  # Empty block: should be skipped
                ],
            ),
            Section(
                heading="2. Empty Section",
                level=1,
                blocks=[],  # Zero content: section must be skipped entirely
            ),
        ],
    )
    out_docx = tmp_path / "test_empty_skipped.docx"
    render_docx(doc_model, DOCX_TEMPLATE, out_path=out_docx)
    doc = docx.Document(out_docx)
    headings = [p.text for p in doc.paragraphs if p.style and p.style.name.startswith("Heading")]
    assert "1. Valid Section" in headings
    assert "2. Empty Section" not in headings

    # 2. PPTX empty slide skipping
    deck_model = DeckModel(
        title="Test Deck",
        slides=[
            SlideModel(role="title", title="Slide 1", subtitle="Sub"),
            SlideModel(role="title_content", title="", bullets=[]),  # Zero content: should be skipped
            SlideModel(role="title_content", title="Slide 3", bullets=[BulletItem(text="Bullet content", level=0)]),
        ],
    )
    out_pptx = tmp_path / "test_empty_skipped.pptx"
    render_pptx(deck_model, PPTX_TEMPLATE, out_path=out_pptx)
    prs = pptx.Presentation(out_pptx)
    assert len(prs.slides) == 2


def test_research_report_no_timeline_pricing():
    """Test that a research_report plan on a proposal-template workspace produces no Timeline/Pricing section."""
    from app.agents.doc_generator import generate_document_model
    from app.agents.supervisor import parse_plan

    plan_report = parse_plan("make a report explaining X's developments")
    assert plan_report.document_type == "research_report"

    plan_prop = parse_plan("create a proposal for X")
    assert plan_prop.document_type == "proposal"

    proposal_profile = analyze_document(DOCX_TEMPLATE)
    assert any("timeline" in item.get("heading", "").lower() or "pricing" in item.get("heading", "").lower() for item in proposal_profile.doc_style.outline)

    # Generate document with research_report document_type
    doc_model = generate_document_model(
        brief="Explain developments in quantum computing",
        profile=proposal_profile,
        document_type=plan_report.document_type,
    )
    section_headings = [s.heading.lower() for s in doc_model.sections]
    assert not any("timeline" in h or "pricing" in h or "commercial" in h for h in section_headings)


def test_auto_decoration_applied_when_dec_score_zero(tmp_path: Path):
    """Test that slides on zero-decoration layouts receive auto-decoration and decorated layouts are untouched."""
    profile = analyze_presentation(PPTX_TEMPLATE)
    dec_scores = {l.index: l.decoration_score for l in profile.ppt_style.layouts}

    deck_model = DeckModel(
        title="Decoration Test Deck",
        slides=[
            # Role 'title' resolves to 1_Title Slide (index 0, dec_score=30)
            SlideModel(role="title", title="Decorated Title Slide", subtitle="Should not have auto-decoration"),
            # Role 'title_content' resolves to Title and Content (index 9, dec_score=0)
            SlideModel(
                role="title_content",
                title="Plain Layout Content Slide",
                bullets=[BulletItem(text="First key strategic initiative", level=0)],
            ),
        ],
    )

    out_file = tmp_path / "test_auto_dec.pptx"
    render_pptx(deck_model, PPTX_TEMPLATE, profile=profile, out_path=out_file)
    assert out_file.exists()

    prs = pptx.Presentation(out_file)
    assert len(prs.slides) == 2

    # Slide 1 (1_Title Slide, dec_score > 0): untouched
    slide1_auto_shapes = [s for s in prs.slides[0].shapes if getattr(s, "name", "").startswith("AutoDecoration")]
    assert len(slide1_auto_shapes) == 0, "Slide 1 with dec_score > 0 should not have auto-decoration"

    # Slide 2 (Title and Content, dec_score == 0): auto-decoration applied
    slide2_auto_shapes = [s for s in prs.slides[1].shapes if getattr(s, "name", "").startswith("AutoDecoration")]
    assert len(slide2_auto_shapes) >= 1, "Slide 2 with dec_score == 0 must have auto-decoration"

    # Verify z-order: auto-decoration shapes should be near the start of _spTree (before placeholder shapes)
    sp_tree_children = [c.tag.split("}")[-1] for c in prs.slides[1].shapes._spTree]
    accent_bar_idx = -1
    first_ph_idx = -1
    for idx, c in enumerate(prs.slides[1].shapes._spTree):
        shape_name = c.find(".//{http://schemas.openxmlformats.org/drawingml/2006/main}cNvPr")
        if shape_name is not None and shape_name.get("name", "").startswith("AutoDecoration"):
            accent_bar_idx = idx
            break
    for idx, shape in enumerate(prs.slides[1].shapes):
        if shape.is_placeholder:
            for tree_idx, c in enumerate(prs.slides[1].shapes._spTree):
                if c == shape.element:
                    first_ph_idx = tree_idx
                    break
            break
    if accent_bar_idx != -1 and first_ph_idx != -1:
        assert accent_bar_idx < first_ph_idx, "Auto-decoration shape must be positioned behind placeholders in z-order"


def test_artifact_version_stores_pptx_file_type():
    """Test BUG 2 fix: node_finalize must save file_type='pptx' for rendered PPTX artifacts."""
    from app.agents.graph import _get_sync_session, node_finalize
    from app.models.artifact import ArtifactVersion

    state = {
        "deck_model": {
            "title": "FileType PPTX Regression Deck",
            "slides": [
                {"role": "title", "title": "Deck Title", "subtitle": "Subtitle"}
            ],
        },
        "template_profiles": {
            "ppt_template_path": str(PPTX_TEMPLATE),
        },
        "registry": {},
    }

    res = node_finalize(state)
    assert res is not None

    sess = _get_sync_session()
    assert sess is not None
    try:
        versions = sess.query(ArtifactVersion).all()
        pptx_vers = [v for v in versions if v.file_path.endswith(".pptx")]
        assert len(pptx_vers) > 0, "Expected at least one PPTX ArtifactVersion record"
        for v in pptx_vers:
            assert v.file_type == "pptx", f"Expected file_type='pptx', found '{v.file_type}'"
    finally:
        sess.close()


def test_markdown_sanitization_in_renderers(tmp_path: Path):
    """Test that sample strings with markdown (**bold**, __x__) are completely stripped in rendered output."""
    from app.services.docx_renderer import strip_markdown as docx_strip_md
    from app.services.pptx_renderer import strip_markdown as pptx_strip_md

    sample_md = "**Critical Risk**: The system has __high__ latency with **immediate** impact."
    cleaned_docx = docx_strip_md(sample_md)
    cleaned_pptx = pptx_strip_md(sample_md)

    assert cleaned_docx == "Critical Risk: The system has high latency with immediate impact."
    assert cleaned_pptx == "Critical Risk: The system has high latency with immediate impact."
    assert "**" not in cleaned_docx and "__" not in cleaned_docx
    assert "**" not in cleaned_pptx and "__" not in cleaned_pptx


def test_duplicate_submission_blocked_within_five_seconds():
    """Test UI logic: duplicate submission of identical message within 5s is ignored."""
    from unittest.mock import patch
    import streamlit as st
    from ui.streamlit_app import _send_message

    st.session_state["chat_history"] = []
    st.session_state["selected_file_ids"] = []
    st.session_state["session_id"] = None
    st.session_state["request_in_flight"] = False
    st.session_state["last_submission_text"] = ""
    st.session_state["last_submission_time"] = 0.0

    with patch("ui.streamlit_app._post") as mock_post:
        mock_post.return_value = {"run_id": "mock_run_1"}
        with patch("ui.streamlit_app._poll_run") as mock_poll:
            mock_poll.return_value = {"status": "done", "reply": "Acknowledged"}

            # First submission
            _send_message("Generate proposal on AI trends")
            assert len(st.session_state.chat_history) == 2

            # Identical resubmission immediately within 5 seconds
            _send_message("Generate proposal on AI trends")
            assert len(st.session_state.chat_history) == 2, "Identical submission within 5 seconds should be ignored"



