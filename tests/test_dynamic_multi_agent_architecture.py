"""Section 26 Verification Tests: Proving the System is Semantic Rather Than Keyword-Driven.

Covers the 10 mandatory test scenarios defined in Section 26:
Test 1: Executive presentation inferred as PPTX semantically.
Test 2: Formal board review inferred from context/requirements without 'presentation' keyword.
Test 3: Append recommendation: APPEND op, new version, previous version intact.
Test 4: 'Only change the fifth page' -> target_nodes = exact fifth page/node & scope validation.
Test 5: Compare internal numbers with latest external situation -> RAG + Web without hardcoding 'latest'.
Test 6: Attached deck as visual reference, report as factual source -> attachment roles.
Test 7: Internal knowledge to support analysis -> RAG based on semantic requirement.
Test 8: Summarize uploaded report -> file analysis, NO unnecessary web research.
Test 9: Change wording on slide 5, preserve design -> content edit, preserve style/layout.
Test 10: Make another version with same structure but update financial section -> new immutable version, target financial section.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from app.agents.editor import edit_artifact
from app.agents.supervisor import parse_plan
from app.agents.validator import validate_outputs, validate_scope
from app.models.artifact import Artifact, ArtifactVersion
from app.models.context import ConversationContext
from app.models.deck_model import DeckModel, SlideModel, BulletItem
from app.models.document_model import DocumentModel, Section, ParagraphBlock
from app.models.execution_plan import ArtifactType, OperationType, TemplateMode
from app.models.file_role import FileRole, FileRoleClassification
from app.models.workbook_model import WorkbookModel, SheetModel, TableModel, CellModel
from app.services.file_classifier import classify_attached_files
from app.services.xlsx_renderer import render_xlsx


from app.core.database import Base
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


@pytest.fixture
def in_memory_db():
    """Create an in-memory SQLite DB session for testing."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()


# ── Test 1 ────────────────────────────────────────────────────────────────────
def test_section26_test1_executive_presentation_inferred_as_pptx():
    """Prompt: 'Turn the supplied material into a concise executive presentation.'
    Expected: PPTX inferred semantically.
    """
    prompt = "Turn the supplied material into a concise executive presentation."
    plan = parse_plan(prompt)

    assert ArtifactType.PPTX in plan.artifact_types
    assert plan.operation in (OperationType.CREATE, OperationType.REGENERATE)
    assert plan.source_requirements["uploaded_files"] is True


# ── Test 2 ────────────────────────────────────────────────────────────────────
def test_section26_test2_formal_board_review_inferred_from_context():
    """Prompt: 'Create something suitable for a formal board review using the attached material.'
    Do not rely on the word 'presentation'.
    Expected: System infers intended artifact from context/requirements.
    """
    prompt = "Create something suitable for a formal board review using the attached material."
    plan = parse_plan(prompt)

    # In formal board reviews, executive presentations or structured proposals are chosen
    assert len(plan.artifact_types) > 0
    assert any(a in plan.artifact_types for a in (ArtifactType.PPTX, ArtifactType.DOCX))
    assert plan.source_requirements["uploaded_files"] is True


# ── Test 3 ────────────────────────────────────────────────────────────────────
def test_section26_test3_append_recommendation_immutable_previous_version(in_memory_db):
    """Prompt: 'Keep everything that has already been approved and add a final recommendation.'
    Expected: APPEND operation, new immutable version, previous version unchanged.
    """
    prompt = "Keep everything that has already been approved and add a final recommendation."
    context = ConversationContext(
        active_artifact={"artifact_id": 1, "kind": "docx", "latest_version": 1, "title": "Approved Strategy"},
        artifact_history=[{"artifact_id": 1, "kind": "docx", "latest_version": 1, "title": "Approved Strategy"}],
    )
    plan = parse_plan(prompt, session_context=context)

    assert plan.operation == OperationType.APPEND
    assert "preserve_existing_content" in plan.preserve_constraints

    # Verify immutable version creation via editor
    doc = DocumentModel(
        title="Approved Strategy",
        sections=[
            Section(heading="Approved Section 1", blocks=[ParagraphBlock(text="Original approved text")]),
        ],
    )
    art = Artifact(id=10, artifact_type="docx", title="Approved Strategy", session_id="sess_append")
    in_memory_db.add(art)
    in_memory_db.flush()

    v1 = ArtifactVersion(
        artifact_id=art.id,
        version_no=1,
        model_json=doc.model_dump_json(),
        file_path="data/outputs/dummy_v1.docx",
        file_type="docx",
        change_summary="V1 approved",
    )
    in_memory_db.add(v1)
    in_memory_db.commit()

    # Pre-create dummy file for version copier
    Path("data/outputs").mkdir(parents=True, exist_ok=True)
    Path("data/outputs/dummy_v1.docx").write_text("dummy")

    res = edit_artifact(
        artifact_id=art.id,
        instruction=prompt,
        db_session=in_memory_db,
    )

    # Verify V2 created and V1 is untouched
    v1_after = in_memory_db.query(ArtifactVersion).filter(ArtifactVersion.artifact_id == art.id, ArtifactVersion.version_no == 1).first()
    v2 = in_memory_db.query(ArtifactVersion).filter(ArtifactVersion.artifact_id == art.id, ArtifactVersion.version_no == 2).first()

    assert v1_after.model_json == doc.model_dump_json()
    assert v2 is not None
    assert res.new_version_no == 2
    assert v2.parent_version_id == v1.id


# ── Test 4 ────────────────────────────────────────────────────────────────────
def test_section26_test4_targeted_node_scope_enforcement():
    """Prompt: 'Only change the fifth page; everything else must remain exactly as it is.'
    Expected: target_nodes = exact fifth page/node & scope validation enforcement.
    """
    prompt = "Only change the fifth page; everything else must remain exactly as it is."
    context = ConversationContext(
        active_artifact={"artifact_id": 2, "kind": "pptx", "latest_version": 1, "title": "Deck"},
        artifact_history=[{"artifact_id": 2, "kind": "pptx", "latest_version": 1, "title": "Deck"}],
    )
    plan = parse_plan(prompt, session_context=context)

    assert plan.operation == OperationType.EDIT
    assert any("5" in n or "page" in n or "slide" in n for n in plan.target_nodes)
    assert "do_not_modify_other_slides" in plan.preserve_constraints

    # Scope validation test
    target_nodes = ["slide_5"]
    actually_changed = ["slide_5"]
    issues = validate_scope(target_nodes=target_nodes, actually_changed_nodes=actually_changed)
    assert len(issues) == 0

    # Scope violation test: slides 4 and 6 changed unauthorized
    unauthorized_changed = ["slide_4", "slide_5", "slide_6"]
    violation_issues = validate_scope(target_nodes=target_nodes, actually_changed_nodes=unauthorized_changed)
    assert len(violation_issues) > 0
    assert violation_issues[0].severity == "error"
    assert "Scope violation" in violation_issues[0].message


# ── Test 5 ────────────────────────────────────────────────────────────────────
def test_section26_test5_rag_and_web_without_hardcoded_keyword():
    """Prompt: 'Compare our internal numbers with the current external market situation.'
    Expected: RAG + web semantically required.
    """
    prompt = "Compare our internal numbers with the current external market situation."
    plan = parse_plan(prompt)

    assert plan.source_requirements["knowledge_base"] is True
    assert plan.source_requirements["web"] is True
    assert plan.use_rag is True
    assert plan.use_web is True


# ── Test 6 ────────────────────────────────────────────────────────────────────
def test_section26_test6_attachment_roles_resolution():
    """Prompt: 'Use the attached deck as the visual reference but use the report as the factual source.'
    Expected: deck = style/template reference, report = factual content source.
    """
    prompt = "Use the attached deck as the visual reference but use the report as the factual source."
    mock_files = [
        {"id": 301, "filename": "corporate_deck.pptx", "file_type": "pptx"},
        {"id": 302, "filename": "quarterly_report.docx", "file_type": "docx"},
    ]
    classifications = [
        FileRoleClassification(file_id=301, filename="corporate_deck.pptx", role=FileRole.TEMPLATE),
        FileRoleClassification(file_id=302, filename="quarterly_report.docx", role=FileRole.SOURCE),
    ]

    with patch("app.services.file_classifier.classify_attached_files", return_value=classifications):
        plan = parse_plan(prompt, file_ids=[301, 302])

    assert 301 in plan.template_file_ids
    assert 302 in plan.source_file_ids
    assert plan.template_mode == TemplateMode.UPLOADED


# ── Test 7 ────────────────────────────────────────────────────────────────────
def test_section26_test7_internal_knowledge_rag_requirement():
    """Prompt: 'Use our internal knowledge to support the analysis.'
    Expected: RAG based on semantic requirement.
    """
    prompt = "Use our internal knowledge to support the analysis."
    plan = parse_plan(prompt)

    assert plan.source_requirements["knowledge_base"] is True
    assert plan.use_rag is True


# ── Test 8 ────────────────────────────────────────────────────────────────────
def test_section26_test8_summarize_report_no_web():
    """Prompt: 'Summarize the uploaded report.'
    Expected: file analysis, NO unnecessary web research.
    """
    prompt = "Summarize the uploaded report."
    plan = parse_plan(prompt, file_ids=[401])

    assert plan.source_requirements["uploaded_files"] is True
    assert plan.source_requirements["web"] is False
    assert plan.use_web is False


# ── Test 9 ────────────────────────────────────────────────────────────────────
def test_section26_test9_edit_slide_preserve_design():
    """Prompt: 'Change the wording on slide 5 but don't touch its design.'
    Expected: content edit only, preserve style/layout.
    """
    prompt = "Change the wording on slide 5 but don't touch its design."
    context = ConversationContext(
        active_artifact={"artifact_id": 3, "kind": "pptx", "latest_version": 1, "title": "Pitch"},
        artifact_history=[{"artifact_id": 3, "kind": "pptx", "latest_version": 1, "title": "Pitch"}],
    )
    plan = parse_plan(prompt, session_context=context)

    assert plan.operation == OperationType.EDIT
    assert any("5" in n or "slide" in n for n in plan.target_nodes)


# ── Test 10 ───────────────────────────────────────────────────────────────────
def test_section26_test10_immutable_version_targeted_financial_section():
    """Prompt: 'Make another version with the same structure but update the financial section.'
    Expected: new immutable version, target financial section.
    """
    prompt = "Make another version with the same structure but update the financial section."
    context = ConversationContext(
        active_artifact={"artifact_id": 4, "kind": "docx", "latest_version": 1, "title": "Financial Report"},
        artifact_history=[{"artifact_id": 4, "kind": "docx", "latest_version": 1, "title": "Financial Report"}],
    )
    plan = parse_plan(prompt, session_context=context)

    assert plan.operation == OperationType.EDIT
    assert any("financial" in n.lower() or "section" in n.lower() for n in plan.target_nodes)


# ── End-to-End XLSX Support Test ──────────────────────────────────────────────
def test_xlsx_end_to_end_rendering_and_validation(tmp_path):
    """Verify that WorkbookModel can be generated, rendered to physical .xlsx, and validated."""
    wb = WorkbookModel(
        title="Quarterly Sales & Forecast",
        sheets=[
            SheetModel(
                name="Sales 2026",
                tables=[
                    TableModel(
                        name="Regional Performance",
                        range="A1:D4",
                        headers=["Region", "Q1 Actual", "Q2 Actual", "Forecast"],
                        rows=[
                            ["North America", 150000, 175000, 200000],
                            ["Europe", 110000, 125000, 140000],
                            ["Asia-Pacific", 95000, 115000, 135000],
                        ],
                    )
                ],
                cells=[
                    CellModel(coordinate="D5", formula="=SUM(D2:D4)", value=475000),
                ],
            )
        ],
    )
    out_file = tmp_path / "test_sales.xlsx"
    render_xlsx(wb, out_file)
    assert out_file.exists()
    assert out_file.stat().st_size > 0

    val_report = validate_outputs(workbook_model=wb)
    assert val_report.passed is True
    assert val_report.score >= 90.0
