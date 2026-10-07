"""Automated test scenarios for PART 3: File Intelligence, Research, RAG, Generation & Natural Chat Interface."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch
import pytest

from app.agents.supervisor import (
    ArtifactType,
    ExecutionPlan,
    OperationType,
    TemplateMode,
    parse_plan,
)
from app.models.deck_model import DeckModel, SlideModel
from app.models.file import UploadedFile
from app.models.file_role import FileRole, FileRoleClassification
from app.services.artifact_ops_engine import apply_operation
from app.services.file_classifier import classify_attached_files
from app.services.template_resolution import resolve_templates
from app.services.versioning import add_version, create_artifact


# ── Scenario 1 ────────────────────────────────────────────────────────────────
def test_scenario_1_attachment_template_creation():
    """Test 1:
    Attachment: proposal_template.docx
    Prompt: 'Create a proposal using the attached template.'
    Expected: DOCX, UPLOADED TEMPLATE
    """
    mock_files = [{"id": 101, "filename": "proposal_template.docx", "file_type": "docx"}]
    prompt = "Create a proposal using the attached template."

    classifications = classify_attached_files(mock_files, user_prompt=prompt)
    assert len(classifications) == 1
    assert classifications[0].role == FileRole.TEMPLATE

    with patch("app.services.file_classifier.classify_attached_files", return_value=classifications):
        plan = parse_plan(prompt, file_ids=[101])

    assert ArtifactType.DOCX in plan.artifact_types
    assert plan.template_mode == TemplateMode.UPLOADED
    assert plan.template_file_ids == [101]


# ── Scenario 2 ────────────────────────────────────────────────────────────────
def test_scenario_2_attachment_pdf_source():
    """Test 2:
    Attachment: research.pdf
    Prompt: 'Create a report using information from the attached PDF.'
    Expected: PDF = SOURCE
    """
    mock_files = [{"id": 102, "filename": "research.pdf", "file_type": "pdf"}]
    prompt = "Create a report using information from the attached PDF."

    classifications = classify_attached_files(mock_files, user_prompt=prompt)
    assert len(classifications) == 1
    assert classifications[0].role == FileRole.SOURCE

    with patch("app.services.file_classifier.classify_attached_files", return_value=classifications):
        plan = parse_plan(prompt, file_ids=[102])

    assert ArtifactType.DOCX in plan.artifact_types
    assert 102 in plan.source_file_ids
    assert plan.template_mode == TemplateMode.DEFAULT


# ── Scenario 3 ────────────────────────────────────────────────────────────────
def test_scenario_3_template_plus_source():
    """Test 3:
    Attachments: template.docx, research.pdf
    Prompt: 'Using the attached research, create a report using the attached template.'
    Expected: template.docx -> TEMPLATE, research.pdf -> SOURCE
    """
    mock_files = [
        {"id": 103, "filename": "template.docx", "file_type": "docx"},
        {"id": 104, "filename": "research.pdf", "file_type": "pdf"},
    ]
    prompt = "Using the attached research, create a report using the attached template."

    classifications = classify_attached_files(mock_files, user_prompt=prompt)
    roles = {c.filename: c.role for c in classifications}
    assert roles["template.docx"] == FileRole.TEMPLATE
    assert roles["research.pdf"] == FileRole.SOURCE

    with patch("app.services.file_classifier.classify_attached_files", return_value=classifications):
        plan = parse_plan(prompt, file_ids=[103, 104])

    assert plan.template_file_ids == [103]
    assert 104 in plan.source_file_ids
    assert plan.template_mode == TemplateMode.UPLOADED
    assert ArtifactType.DOCX in plan.artifact_types


# ── Scenario 4 ────────────────────────────────────────────────────────────────
def test_scenario_4_web_research_inference():
    """Test 4:
    Prompt: 'Create a report using the latest Gen AI trends in 2026.'
    Expected: use_web = true
    """
    prompt = "Create a report using the latest Gen AI trends in 2026."
    plan = parse_plan(prompt)

    assert plan.use_web is True
    assert ArtifactType.DOCX in plan.artifact_types


# ── Scenario 5 ────────────────────────────────────────────────────────────────
def test_scenario_5_explicit_no_web():
    """Test 5:
    Prompt: 'Create a report using only the attached document. Do not search the web.'
    Expected: use_web = false
    """
    prompt = "Create a report using only the attached document. Do not search the web."
    plan = parse_plan(prompt)

    assert plan.use_web is False
    assert plan.use_rag is False


# ── Scenario 6 ────────────────────────────────────────────────────────────────
def test_scenario_6_rag_inference():
    """Test 6:
    Prompt: 'Use the company knowledge base and create a proposal.'
    Expected: use_rag = true
    """
    prompt = "Use the company knowledge base and create a proposal."
    plan = parse_plan(prompt)

    assert plan.use_rag is True
    assert ArtifactType.DOCX in plan.artifact_types


# ── Scenario 7 ────────────────────────────────────────────────────────────────
def test_scenario_7_all_sources_combined():
    """Test 7:
    Prompt: 'Create a report using the attached document, company knowledge base, and latest web research.'
    Expected: uploaded source = true, RAG = true, WEB = true
    """
    mock_files = [{"id": 105, "filename": "strategy.docx", "file_type": "docx"}]
    prompt = "Create a report using the attached document, company knowledge base, and latest web research."

    classifications = classify_attached_files(mock_files, user_prompt=prompt)
    assert classifications[0].role == FileRole.SOURCE

    with patch("app.services.file_classifier.classify_attached_files", return_value=classifications):
        plan = parse_plan(prompt, file_ids=[105])

    assert 105 in plan.source_file_ids
    assert plan.use_rag is True
    assert plan.use_web is True


# ── Scenario 8 ────────────────────────────────────────────────────────────────
def test_scenario_8_missing_attachment_state():
    """Test 8:
    No file attached.
    Prompt: 'Create a proposal using the attached template.'
    Expected: clear missing-attachment state (TEMPLATE_FILE_REQUIRED)
    """
    prompt = "Create a proposal using the attached template."
    plan = parse_plan(prompt, file_ids=[])

    assert plan.template_mode == TemplateMode.UPLOADED
    assert plan.template_file_ids == []

    # Resolving templates with empty file list raises clear validation error
    tmpl_res = resolve_templates(
        template_mode=plan.template_mode,
        artifact_types=plan.artifact_types,
        file_ids=[],
        uploaded_files=[],
        user_instruction=prompt,
    )
    assert tmpl_res.is_valid is False
    assert tmpl_res.error_code == "TEMPLATE_FILE_REQUIRED"


# ── Scenario 9 ────────────────────────────────────────────────────────────────
def test_scenario_9_multiple_attachments_classification():
    """Test 9:
    Multiple attachments: template.docx, research.pdf, competitor.pdf
    Prompt: 'Create a proposal using the attached template and research documents.'
    Expected: correct role classification:
      template.docx -> TEMPLATE
      research.pdf -> SOURCE
      competitor.pdf -> SOURCE
    """
    mock_files = [
        {"id": 106, "filename": "template.docx", "file_type": "docx"},
        {"id": 107, "filename": "research.pdf", "file_type": "pdf"},
        {"id": 108, "filename": "competitor.pdf", "file_type": "pdf"},
    ]
    prompt = "Create a proposal using the attached template and research documents."

    classifications = classify_attached_files(mock_files, user_prompt=prompt)
    role_map = {c.filename: c.role for c in classifications}

    assert role_map["template.docx"] == FileRole.TEMPLATE
    assert role_map["research.pdf"] == FileRole.SOURCE
    assert role_map["competitor.pdf"] == FileRole.SOURCE

    with patch("app.services.file_classifier.classify_attached_files", return_value=classifications):
        plan = parse_plan(prompt, file_ids=[106, 107, 108])

    assert plan.template_file_ids == [106]
    assert 107 in plan.source_file_ids
    assert 108 in plan.source_file_ids
    assert plan.template_mode == TemplateMode.UPLOADED


# ── Scenario 10 ───────────────────────────────────────────────────────────────
def test_scenario_10_conversational_editing_v1_v2_v3():
    """Test 10:
    Conversation:
      1. Create a PPT about Gen AI -> V1
      2. Change slide 4 -> V2
      3. Append a conclusion -> V3
    Expected: V1 -> V2 -> V3 immutable chain with content preservation.
    """
    # 1. Turn 1: Generate V1
    from app.models.deck_model import BulletItem
    slides = [
        SlideModel(node_id=f"slide_{i}", title=f"Slide {i}", bullets=[BulletItem(text=f"Content {i}")])
        for i in range(1, 6)
    ]
    v1_model = DeckModel(title="Gen AI Presentation", slides=slides)
    assert len(v1_model.slides) == 5

    # 2. Turn 2: Edit slide 4
    plan_v2 = parse_plan(
        "Change slide 4 to focus more on enterprise applications.",
        session_context={"active_artifact": {"kind": "pptx", "artifact_id": 1, "latest_version": 1}},
        session_artifacts=[{"kind": "pptx", "artifact_id": 1, "latest_version": 1, "active": True}],
    )
    assert plan_v2.operation == OperationType.EDIT
    assert "slide 4" in plan_v2.target_nodes

    v2_result = apply_operation(
        model=v1_model,
        operation=OperationType.EDIT,
        target_node_id=v1_model.slides[3].node_id,
        payload={"bullets": [BulletItem(text="Enterprise ROI"), BulletItem(text="Data Security & Governance")]},
    )
    v2_model = v2_result.updated_model
    # Check slide 4 was updated
    assert [b.text for b in v2_model.slides[3].bullets] == ["Enterprise ROI", "Data Security & Governance"]
    # Check other slides were preserved
    assert v2_model.slides[0].title == "Slide 1"
    assert v2_model.slides[1].title == "Slide 2"
    assert v2_model.slides[2].title == "Slide 3"
    assert v2_model.slides[4].title == "Slide 5"
    assert len(v2_model.slides) == 5

    # 3. Turn 3: Append conclusion slide
    plan_v3 = parse_plan(
        "Append a conclusion slide.",
        session_context={"active_artifact": {"kind": "pptx", "artifact_id": 1, "latest_version": 2}},
        session_artifacts=[{"kind": "pptx", "artifact_id": 1, "latest_version": 2, "active": True}],
    )
    assert plan_v3.operation == OperationType.APPEND

    v3_result = apply_operation(
        model=v2_model,
        operation=OperationType.APPEND,
        target_node_id=None,
        payload={"title": "Conclusion & Next Steps", "bullets": [BulletItem(text="Action plan"), BulletItem(text="Timeline")]},
    )
    v3_model = v3_result.updated_model
    assert len(v3_model.slides) == 6
    assert v3_model.slides[-1].title == "Conclusion & Next Steps"
    # Content of previous slides preserved
    assert [b.text for b in v3_model.slides[3].bullets] == ["Enterprise ROI", "Data Security & Governance"]




# ── Section 8 Ambiguity Test ──────────────────────────────────────────────────
def test_ambiguity_rule_multiple_candidate_templates():
    """Section 8 Ambiguity Rule:
    User attaches template1.docx and template2.docx.
    Prompt: 'Create a proposal using the attached files.'
    The system should identify ambiguity rather than silently picking one.
    """
    mock_files = [
        {"id": 201, "filename": "template1.docx", "file_type": "docx"},
        {"id": 202, "filename": "template2.docx", "file_type": "docx"},
    ]
    prompt = "Create a proposal using the attached files."
    classifications = classify_attached_files(mock_files, user_prompt=prompt)

    # Both templates detected and flagged as ambiguous
    assert any(c.is_ambiguous for c in classifications)

    with patch("app.services.file_classifier.classify_attached_files", return_value=classifications):
        plan = parse_plan(prompt, file_ids=[201, 202])

    assert plan.error == "AMBIGUOUS_TEMPLATES"
    assert "template1.docx" in plan.reasoning
    assert "template2.docx" in plan.reasoning
