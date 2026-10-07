"""Focused tests for Part 1: Intelligence & Context Layer."""

from __future__ import annotations

import json
from unittest.mock import MagicMock
import pytest

from app.agents.supervisor import (
    ArtifactType,
    ExecutionPlan,
    OperationType,
    TemplateMode,
    parse_plan,
)
from app.models.context import ConversationContext
from app.services.reference_resolver import resolve_references


# 1. Create PPT request
def test_create_ppt_request():
    """Input: 'Create a 10-slide presentation about Generative AI.'"""
    plan = parse_plan("Create a 10-slide presentation about Generative AI.")

    assert plan.operation == OperationType.CREATE
    assert ArtifactType.PPTX in plan.artifact_types
    assert plan.slide_count == 10
    assert plan.template_mode == TemplateMode.DEFAULT
    assert plan.use_web is False
    assert plan.use_rag is False
    # Backward compatibility checks
    assert plan.action == "generate"
    assert "pptx" in plan.outputs


# 2. Create DOCX from template
def test_create_docx_from_template():
    """Input: 'Create a proposal using the uploaded template.'"""
    plan = parse_plan("Create a proposal using the uploaded template.")

    assert plan.operation == OperationType.CREATE
    assert ArtifactType.DOCX in plan.artifact_types
    assert plan.template_mode == TemplateMode.UPLOADED
    assert plan.action == "generate"
    assert "docx" in plan.outputs


# 3. Create XLSX
def test_create_xlsx():
    """Input: 'Create an Excel workbook showing monthly revenue.'"""
    plan = parse_plan("Create an Excel workbook showing monthly revenue.")

    assert plan.operation == OperationType.CREATE
    assert ArtifactType.XLSX in plan.artifact_types


# 4. Web research
def test_web_research_inference():
    """Input: 'Update the presentation with the latest GenAI trends.'"""
    session_ctx = {
        "artifacts": [
            {"artifact_id": 101, "kind": "pptx", "title": "GenAI Deck", "latest_version": 1, "active": True}
        ]
    }
    plan = parse_plan("Update the presentation with the latest GenAI trends.", session_context=session_ctx)

    assert plan.use_web is True
    assert plan.operation in (OperationType.EDIT, OperationType.REFRESH)
    assert ArtifactType.PPTX in plan.artifact_types
    assert plan.target_artifact_id == 101


# 5. Uploaded source
def test_uploaded_source_recording():
    """Input: 'Use the information from the uploaded PDF.'"""
    plan = parse_plan("Use the information from the uploaded PDF.", file_ids=[42])

    assert any("pdf" in ref.lower() for ref in plan.source_references)
    assert 42 in plan.source_file_ids


# 6. Edit
def test_edit_specific_slide():
    """Input: 'Make slide 5 more concise.'"""
    session_ctx = {
        "artifacts": [
            {"artifact_id": 55, "kind": "pptx", "title": "Tech Architecture", "latest_version": 1, "active": True}
        ]
    }
    plan = parse_plan("Make slide 5 more concise.", session_context=session_ctx)

    assert plan.operation == OperationType.EDIT
    assert any("slide 5" in node.lower() for node in plan.target_nodes)
    assert plan.target_artifact_id == 55
    assert ArtifactType.PPTX in plan.artifact_types


# 7. Append
def test_append_operation():
    """Input: 'Append a conclusion to the document.'"""
    session_ctx = {
        "artifacts": [
            {"artifact_id": 88, "kind": "docx", "title": "Project Proposal", "latest_version": 1, "active": True}
        ]
    }
    plan = parse_plan("Append a conclusion to the document.", session_context=session_ctx)

    assert plan.operation == OperationType.APPEND
    assert "preserve_existing_content" in plan.preserve_constraints
    assert plan.target_artifact_id == 88


# 8. Regenerate
def test_regenerate_operation():
    """Input: 'Regenerate the presentation using the same template.'"""
    session_ctx = {
        "artifacts": [
            {"artifact_id": 77, "kind": "pptx", "title": "Quarterly Deck", "latest_version": 2, "active": True}
        ]
    }
    plan = parse_plan("Regenerate the presentation using the same template.", session_context=session_ctx)

    assert plan.operation == OperationType.REGENERATE
    assert plan.template_mode == TemplateMode.EXISTING
    assert "preserve_style" in plan.preserve_constraints
    assert plan.target_artifact_id == 77


# 9. Context reference resolution
def test_pronoun_reference_resolution():
    """User: 'Create a presentation about AI.' -> Active presentation. Followed by: 'Make it shorter.'"""
    session_ctx = {
        "active_topic": "Artificial Intelligence",
        "artifacts": [
            {"artifact_id": 12, "kind": "pptx", "title": "AI Presentation", "latest_version": 1, "active": True}
        ],
        "active_artifact": {"artifact_id": 12, "kind": "pptx", "title": "AI Presentation", "latest_version": 1},
    }

    refs = resolve_references("Make it shorter.", session_ctx)
    assert refs.target_artifact_id == 12
    assert refs.target_artifact_type == ArtifactType.PPTX
    assert "presentation" in refs.resolved_message.lower()

    plan = parse_plan("Make it shorter.", session_context=session_ctx)
    assert plan.target_artifact_id == 12
    assert ArtifactType.PPTX in plan.artifact_types
    assert plan.operation == OperationType.EDIT


# 10. Override previous requirement
def test_override_previous_requirement():
    """Previous: 'Create 10 slides.' Current: 'Actually make it 15 slides.'"""
    session_ctx = {
        "extracted_requirements": {"slide_count": 10},
        "artifacts": [
            {"artifact_id": 12, "kind": "pptx", "title": "Deck", "latest_version": 1, "active": True}
        ],
    }
    plan = parse_plan("Actually make it 15 slides.", session_context=session_ctx)
    assert plan.slide_count == 15


# 11. Ambiguous request without active artifact
def test_ambiguous_request_without_active_artifact():
    """Input: 'Make it better.' without existing artifacts."""
    empty_ctx = {"artifacts": []}
    plan = parse_plan("Make it better.", session_context=empty_ctx)

    assert ArtifactType.UNKNOWN in plan.artifact_types
    assert plan.confidence < 1.0


# 12. Negative web research constraint
def test_explicit_no_web_search():
    """Input: 'Create a report about market trends but do not search the web.'"""
    plan = parse_plan("Create a report about market trends but do not search the web.")
    assert plan.use_web is False
