"""Automated unit and integration tests for Step 7: LangGraph supervisor, validator, and chat endpoints."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from app.agents.graph import graph_app, route_intent, should_retry
from app.agents.state import GraphState
from app.agents.supervisor import Plan, parse_plan
from app.agents.validator import ValidationIssue, ValidationReport, validate_outputs
from app.models.deck_model import BulletItem, DeckModel, SlideModel
from app.models.document_model import BulletsBlock, DocumentModel, ParagraphBlock, Section, TableBlock
from app.services.vector_store import InMemoryStore, set_vector_store_override


@pytest.fixture(autouse=True)
def use_in_memory_store():
    """Ensure tests run using InMemoryStore."""
    store = InMemoryStore()
    set_vector_store_override(store)
    yield store
    set_vector_store_override(None)


# 1. Routing per Intent Tests
def test_supervisor_plan_parsing():
    """Test intent parsing for 'generate', 'edit', 'convert', and 'answer'."""
    # Test regex slide count parsing
    p1 = parse_plan("Create a 15-slide presentation deck on Generative AI")
    assert p1.slide_count == 15
    assert "pptx" in p1.outputs

    state_gen: GraphState = {"plan": {"action": "generate"}}
    assert route_intent(state_gen) == "analyze_templates_node"

    state_edit: GraphState = {"plan": {"action": "edit"}}
    assert route_intent(state_edit) == "stub_node"

    state_convert: GraphState = {"plan": {"action": "convert"}}
    assert route_intent(state_convert) == "stub_node"

    state_ans: GraphState = {"plan": {"action": "answer"}}
    assert route_intent(state_ans) == "answer_node"


# 2. Validator Catching Each Issue Type
def test_validator_issue_detection():
    """Test deterministic validator catching slide count mismatch, placeholders, and missing citations."""
    # Create deck with 11 slides when 12 expected, and placeholder text
    deck = DeckModel(
        title="Test Deck",
        slides=[
            SlideModel(role="title", title="Title Slide [Placeholder]", subtitle="Sub", source_ids=[]),
            SlideModel(role="title_content", title="Slide 2", bullets=[BulletItem(text="Only one bullet", level=0, source_ids=[])]),
        ]
    )

    doc = DocumentModel(
        title="Test Doc",
        subtitle="Sub",
        client_name="Client",
        date="2026",
        sections=[
            Section(heading="1. Sec 1", level=1, blocks=[ParagraphBlock(text="Lorem ipsum text", source_ids=[])]),
        ]
    )

    report = validate_outputs(
        doc_model=doc,
        deck_model=deck,
        expected_slide_count=12,
        valid_source_ids={1, 2},
    )

    assert report.passed is False
    assert report.score < 80.0
    issue_msgs = [i.message for i in report.issues]
    assert any("Slide count mismatch" in msg for msg in issue_msgs)
    assert any("Placeholder text detected" in msg for msg in issue_msgs)
    assert any("minimum 6 required" in msg for msg in issue_msgs)


# 3. Validation Retry Triggering
def test_validation_retry_logic():
    """Test that a failed validation triggers exactly one retry before finalizing."""
    state_failed_retry0: GraphState = {
        "validation": {"passed": False},
        "retry_count": 0,
        "plan": {"outputs": ["docx", "pptx"]},
    }
    assert should_retry(state_failed_retry0) == "generate_doc_node"
    assert state_failed_retry0["retry_count"] == 1

    state_failed_retry1: GraphState = {
        "validation": {"passed": False},
        "retry_count": 1,
        "plan": {"outputs": ["docx", "pptx"]},
    }
    assert should_retry(state_failed_retry1) == "finalize_node"


# 4. Graceful Degradation on Web Research Failure
def test_web_research_error_degradation():
    """Test that an error in web research degrades gracefully without crashing state."""
    state: GraphState = {
        "run_id": "test_degrade",
        "user_message": "Research AI",
        "plan": {"action": "generate", "use_web": True, "use_kb": False, "topic": "AI"},
        "errors": [],
    }

    with patch("app.agents.graph.build_context", side_effect=RuntimeError("Web search network timeout")):
        from app.agents.graph import node_gather_context
        res_state = node_gather_context(state)

    # Node execution completes gracefully despite exception
    assert "errors" in res_state
    assert len(res_state["errors"]) >= 1
    assert "Web search network timeout" in res_state["errors"][0]


# 5. Full Graph Happy Path (Mocked LLM & Search)
def test_graph_happy_path_produces_artifacts():
    """Test end-to-end execution of graph producing DOCX and PPTX artifacts."""
    state: GraphState = {
        "run_id": "test_happy_path",
        "user_message": "Research Generative AI and create a proposal and 12-slide presentation",
        "file_ids": [],
    }

    res_state = graph_app.invoke(state)

    assert "reply" in res_state
    assert len(res_state.get("reply", "")) > 0
    assert "artifacts" in res_state
    assert len(res_state["artifacts"]) >= 2
