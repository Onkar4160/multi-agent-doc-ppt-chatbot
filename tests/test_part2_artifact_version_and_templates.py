"""Tests for PART 2 — Artifact, Template, File Upload & Version Management.

Verifies:
- Stable node IDs for DOCX, PPTX, and XLSX
- Deterministic target resolution (slide 4, slide 4 title, introduction, conclusion, invalid target error)
- Strict edit scope (only targeted node changes, all other nodes recorded in unchanged_nodes)
- Strict append semantics (existing content unchanged, new content added after)
- Immutable versioning chain (V1 -> V2 -> V3, parent_version_id, diff_json)
- Uploaded template vs uploaded source resolution
- Missing template error (TEMPLATE_FILE_REQUIRED)
- Invalid template type rejection
- Multiple uploaded files resolution
"""

from __future__ import annotations

import json
from pathlib import Path
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models.artifact import Artifact, ArtifactVersion
from app.models.deck_model import BulletItem, DeckModel, SlideModel
from app.models.document_model import (
    BulletsBlock,
    DocumentModel,
    ParagraphBlock,
    Section,
    TableBlock,
)
from app.models.execution_plan import ArtifactType, OperationType, TemplateMode
from app.models.file import UploadedFile
from app.models.workbook_model import CellModel, ChartModel, SheetModel, TableModel, WorkbookModel
from app.services.artifact_nodes import (
    ensure_node_ids,
    find_node,
    list_all_node_ids,
)
from app.services.artifact_ops_engine import apply_operation
from app.services.diffing import diff_models
from app.services.reference_resolver import resolve_references
from app.services.target_resolver import resolve_target
from app.services.template_resolution import resolve_templates
from app.services.versioning import add_version, create_artifact


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def sample_deck() -> DeckModel:
    slides = [
        SlideModel(role="title", title=f"Slide {i} Title", bullets=[BulletItem(text=f"Bullet {i}.1")])
        for i in range(1, 11)
    ]
    deck = DeckModel(title="Generative AI Overview", slides=slides)
    ensure_node_ids(deck)
    return deck


@pytest.fixture
def sample_doc() -> DocumentModel:
    doc = DocumentModel(
        title="Business Proposal",
        sections=[
            Section(
                heading="Introduction",
                blocks=[ParagraphBlock(text="Introductory text about the company.")],
            ),
            Section(
                heading="Methodology",
                blocks=[
                    ParagraphBlock(text="Our implementation strategy."),
                    BulletsBlock(items=["Step 1: Planning", "Step 2: Execution"]),
                ],
            ),
            Section(
                heading="Pricing",
                blocks=[
                    TableBlock(
                        headers=["Item", "Cost"],
                        rows=[["Service A", "$10,000"], ["Service B", "$15,000"]],
                    )
                ],
            ),
        ],
    )
    ensure_node_ids(doc)
    return doc


@pytest.fixture
def sample_workbook() -> WorkbookModel:
    wb = WorkbookModel(
        title="Financial Model",
        sheets=[
            SheetModel(
                name="Revenue",
                cells=[
                    CellModel(coordinate="A1", value="Q1"),
                    CellModel(coordinate="B1", value=150000),
                ],
                tables=[
                    TableModel(
                        name="Q1Revenue",
                        range="A1:B10",
                        headers=["Quarter", "Amount"],
                        rows=[["Q1", 150000]],
                    )
                ],
                charts=[
                    ChartModel(
                        title="Quarterly Growth",
                        chart_type="column",
                        data_range="Revenue!$B$1:$B$10",
                    )
                ],
            )
        ],
    )
    ensure_node_ids(wb)
    return wb


@pytest.fixture
def test_sync_db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()


# ── 1. Artifact Structure & Stable Node IDs ───────────────────────────────────

def test_artifact_structure_docx_node_ids(sample_doc):
    """DOCX structural components receive deterministic stable IDs."""
    assert sample_doc.node_id == "document_001"
    assert sample_doc.sections[0].node_id == "section_001"
    assert sample_doc.sections[1].node_id == "section_002"
    assert sample_doc.sections[0].blocks[0].node_id == "paragraph_001_001"
    assert sample_doc.sections[1].blocks[1].node_id == "bullets_002_002"
    assert sample_doc.sections[2].blocks[0].node_id == "table_003_001"


def test_artifact_structure_pptx_node_ids(sample_deck):
    """PPTX structural components receive deterministic stable IDs."""
    assert sample_deck.node_id == "presentation_001"
    assert len(sample_deck.slides) == 10
    assert sample_deck.slides[0].node_id == "slide_001"
    assert sample_deck.slides[3].node_id == "slide_004"
    assert sample_deck.slides[9].node_id == "slide_010"
    assert sample_deck.slides[0].bullets[0].node_id == "slide_001_bullet_001"


def test_artifact_structure_xlsx_node_ids(sample_workbook):
    """XLSX structural components receive deterministic stable IDs."""
    assert sample_workbook.node_id == "workbook_001"
    sheet = sample_workbook.sheets[0]
    assert sheet.node_id == "sheet_001"
    assert sheet.cells[0].node_id == "cell_A1"
    assert sheet.tables[0].node_id == "table_001_001"
    assert sheet.charts[0].node_id == "chart_001_001"
    assert ArtifactType.XLSX.value == "XLSX"


# ── 2. Target Resolution ──────────────────────────────────────────────────────

def test_target_resolution_slide_and_title(sample_deck):
    """Deterministic resolver targets slide, slide title, or returns explicit error for missing slide."""
    res_slide = resolve_target("slide 4", sample_deck)
    assert res_slide.is_valid is True
    assert res_slide.target_node_id == "slide_004"
    assert res_slide.target_type == "slide"

    res_title = resolve_target("slide 4 title", sample_deck)
    assert res_title.is_valid is True
    assert res_title.target_node_id == "slide_004_title"
    assert res_title.target_type == "slide_title"

    # Slide 12 out of bounds for a 10-slide presentation
    res_invalid = resolve_target("slide 12", sample_deck)
    assert res_invalid.is_valid is False
    assert res_invalid.error_code == "INVALID_TARGET"
    assert "10 slides" in res_invalid.error_message


def test_target_resolution_document_sections(sample_doc):
    """Deterministic resolver targets sections, paragraphs, and tables."""
    res_intro = resolve_target("introduction", sample_doc)
    assert res_intro.is_valid is True
    assert res_intro.target_node_id == "section_001"

    res_sec2 = resolve_target("section 2", sample_doc)
    assert res_sec2.is_valid is True
    assert res_sec2.target_node_id == "section_002"

    res_para = resolve_target("first paragraph", sample_doc)
    assert res_para.is_valid is True
    assert res_para.target_node_id == "paragraph_001_001"

    res_invalid_sec = resolve_target("section 9", sample_doc)
    assert res_invalid_sec.is_valid is False
    assert res_invalid_sec.error_code == "INVALID_TARGET"


def test_conversational_version_references():
    """Resolves conversational references like 'previous version', 'V1', 'latest version'."""
    res1 = resolve_references("update the previous version")
    assert res1.target_version_no is None  # Interpreted relatively

    res2 = resolve_references("revert back to V1")
    assert res2.target_version_no == 1

    res3 = resolve_references("change slide 4 on V2")
    assert res3.target_version_no == 2
    assert "slide 4" in res3.target_nodes


# ── 3. Strict Edit Scope ──────────────────────────────────────────────────────

def test_strict_edit_scope(sample_deck):
    """Editing slide 4 title modifies ONLY slide_004_title; all other nodes are in unchanged_nodes."""
    old_title_slide_1 = sample_deck.slides[0].title
    old_title_slide_5 = sample_deck.slides[4].title

    res = apply_operation(
        model=sample_deck,
        operation=OperationType.EDIT,
        target_node_id="slide_004_title",
        payload="AI Strategy 2026",
    )

    assert res.success is True
    assert len(res.changed_nodes) == 1
    assert res.changed_nodes[0].node_id == "slide_004_title"
    assert res.changed_nodes[0].new_value == "AI Strategy 2026"

    # Verify updated model
    updated_deck = res.updated_model
    assert updated_deck.slides[3].title == "AI Strategy 2026"
    assert updated_deck.slides[0].title == old_title_slide_1
    assert updated_deck.slides[4].title == old_title_slide_5

    # Verify unchanged nodes recording
    assert "slide_001" in res.unchanged_nodes
    assert "slide_002" in res.unchanged_nodes
    assert "slide_003" in res.unchanged_nodes
    assert "slide_005" in res.unchanged_nodes
    assert "slide_004_title" not in res.unchanged_nodes


# ── 4. Append Semantics ───────────────────────────────────────────────────────

def test_append_semantics(sample_doc):
    """Append adds new section strictly AFTER existing sections without modifying previous content."""
    initial_sec_count = len(sample_doc.sections)
    initial_ids = list_all_node_ids(sample_doc)

    res = apply_operation(
        model=sample_doc,
        operation=OperationType.APPEND,
        target_node_id=None,
        payload={"heading": "Conclusion", "paragraphs": ["Final concluding remarks."]},
    )

    assert res.success is True
    updated_doc = res.updated_model
    assert len(updated_doc.sections) == initial_sec_count + 1

    # Appended section appears at the end
    last_sec = updated_doc.sections[-1]
    assert last_sec.heading == "Conclusion"
    assert last_sec.node_id == "section_004"

    # All initial nodes are preserved in unchanged_nodes
    for init_id in initial_ids:
        assert init_id in res.unchanged_nodes

    assert len(res.changed_nodes) == 0
    assert "section_004" in res.added_nodes


# ── 5. Version Management & Immutability ──────────────────────────────────────

def test_version_chain_immutability(sample_deck, tmp_path):
    """V1 -> V2 -> V3 chain records parent_version_id and preserves immutability."""
    # Write dummy rendered PPTX
    v1_file = tmp_path / "v1.pptx"
    v1_file.write_text("v1 binary content")

    v2_file = tmp_path / "v2.pptx"
    v2_file.write_text("v2 binary content")

    # 1. Create top-level Artifact
    from app.models.artifact import Artifact, ArtifactVersion

    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()

    art = Artifact(title="AI Pitch", artifact_type="pptx", session_id="sess_123")
    db.add(art)
    db.commit()

    # V1
    v1_rec = ArtifactVersion(
        artifact_id=art.id,
        version_no=1,
        model_json=sample_deck.model_dump_json(),
        file_path=str(v1_file),
        file_type="pptx",
        parent_version_id=None,
        change_summary="Initial generation",
    )
    db.add(v1_rec)
    db.commit()
    assert v1_rec.id is not None
    assert v1_rec.parent_version_id is None

    # V2 (Edit slide 4 title)
    edit_res = apply_operation(sample_deck, OperationType.EDIT, "slide_004_title", "New Title")
    v2_model = edit_res.updated_model
    diff_v2 = diff_models(sample_deck, v2_model, operation="EDIT")

    v2_rec = ArtifactVersion(
        artifact_id=art.id,
        version_no=2,
        model_json=v2_model.model_dump_json(),
        file_path=str(v2_file),
        file_type="pptx",
        parent_version_id=v1_rec.id,
        change_summary="Edited slide 4 title",
        diff_json=json.dumps(diff_v2),
    )
    db.add(v2_rec)
    db.commit()

    assert v2_rec.version_no == 2
    assert v2_rec.parent_version_id == v1_rec.id

    # V3 (Append slide)
    app_res = apply_operation(v2_model, OperationType.APPEND, None, {"title": "Conclusion Slide"})
    v3_model = app_res.updated_model
    diff_v3 = diff_models(v2_model, v3_model, operation="APPEND")

    v3_rec = ArtifactVersion(
        artifact_id=art.id,
        version_no=3,
        model_json=v3_model.model_dump_json(),
        file_path=str(v2_file),
        file_type="pptx",
        parent_version_id=v2_rec.id,
        change_summary="Appended conclusion slide",
        diff_json=json.dumps(diff_v3),
    )
    db.add(v3_rec)
    db.commit()

    assert v3_rec.version_no == 3
    assert v3_rec.parent_version_id == v2_rec.id

    # Verify V1 remained completely unchanged in DB
    refetched_v1 = db.query(ArtifactVersion).filter(ArtifactVersion.id == v1_rec.id).first()
    v1_loaded_deck = DeckModel.model_validate_json(refetched_v1.model_json)
    assert v1_loaded_deck.slides[3].title == "Slide 4 Title"
    assert refetched_v1.version_no == 1
    assert refetched_v1.parent_version_id is None
    db.close()


# ── 6. Diff Generation ────────────────────────────────────────────────────────

def test_diff_generation_structured(sample_deck):
    """diff_models returns structured changed_nodes, unchanged_nodes, added_nodes."""
    res_edit = apply_operation(sample_deck, OperationType.EDIT, "slide_004_title", "New Title")
    diff = diff_models(sample_deck, res_edit.updated_model, operation="EDIT")

    assert diff["operation"] == "EDIT"
    assert any(cn["node_id"] == "slide_004_title" for cn in diff["changed_nodes"])
    assert "slide_001" in diff["unchanged_nodes"]

    res_append = apply_operation(sample_deck, OperationType.APPEND, None, {"title": "Appended Slide"})
    diff_app = diff_models(sample_deck, res_append.updated_model, operation="APPEND")

    assert diff_app["operation"] == "APPEND"
    assert len(diff_app["added_nodes"]) > 0


# ── 7. Uploaded Template vs Source Resolution ─────────────────────────────────

def test_uploaded_template_resolution():
    """User prompt 'Create a proposal using the uploaded template' resolves to UPLOADED and template_file_id."""
    up_docx = UploadedFile(
        id=42,
        filename="proposal_template.docx",
        file_type="docx",
        file_size=12345,
        stored_path="/storage/uploads/proposal_template.docx",
        purpose="TEMPLATE",
    )

    res = resolve_templates(
        template_mode=TemplateMode.UPLOADED,
        artifact_types=[ArtifactType.DOCX],
        file_ids=[42],
        uploaded_files=[up_docx],
        user_instruction="Create a proposal using the uploaded template.",
    )

    assert res.is_valid is True
    assert res.resolved_mode == "UPLOADED"
    assert res.docx_template_file_id == 42
    assert res.docx_template_path == "/storage/uploads/proposal_template.docx"


def test_missing_uploaded_template_error():
    """Requesting uploaded template without uploading any file returns TEMPLATE_FILE_REQUIRED."""
    res = resolve_templates(
        template_mode=TemplateMode.UPLOADED,
        artifact_types=[ArtifactType.DOCX],
        file_ids=[],
        uploaded_files=[],
        user_instruction="Create a proposal using the uploaded template.",
    )

    assert res.is_valid is False
    assert res.error_code == "TEMPLATE_FILE_REQUIRED"
    assert "no template file has been uploaded" in res.error_message


def test_invalid_template_type_error():
    """Uploading a PNG when DOCX template requested returns INVALID_TEMPLATE_TYPE error."""
    up_png = UploadedFile(
        id=88,
        filename="diagram.png",
        file_type="png",
        file_size=5000,
        stored_path="/storage/uploads/diagram.png",
        purpose="TEMPLATE",
    )

    res = resolve_templates(
        template_mode=TemplateMode.UPLOADED,
        artifact_types=[ArtifactType.DOCX],
        file_ids=[88],
        uploaded_files=[up_png],
        user_instruction="Create a proposal using the uploaded template.",
    )

    assert res.is_valid is False
    assert res.error_code == "INVALID_TEMPLATE_TYPE"
    assert "PNG" in res.error_message


def test_uploaded_reference_not_treated_as_template():
    """Uploading a research PDF records source_file_ids, but does NOT set template_mode to UPLOADED."""
    from app.agents.supervisor import parse_plan

    plan = parse_plan(
        user_message="Create a proposal using information from the uploaded PDF.",
        file_ids=[99],
    )

    assert 99 in plan.source_file_ids
    assert plan.template_mode != TemplateMode.UPLOADED
    assert plan.template_file_ids == []


def test_multiple_uploaded_files_resolution():
    """When multiple files are uploaded, resolve_templates picks the correct template file."""
    f1 = UploadedFile(
        id=1,
        filename="notes.docx",
        file_type="docx",
        file_size=1000,
        stored_path="/storage/uploads/notes.docx",
        purpose="REFERENCE",
    )
    f2 = UploadedFile(
        id=2,
        filename="custom_company_template.docx",
        file_type="docx",
        file_size=2000,
        stored_path="/storage/uploads/custom_company_template.docx",
        purpose="TEMPLATE",
    )

    res = resolve_templates(
        template_mode=TemplateMode.UPLOADED,
        artifact_types=[ArtifactType.DOCX],
        file_ids=[1, 2],
        uploaded_files=[f1, f2],
        user_instruction="Create a proposal using custom company template",
    )

    assert res.is_valid is True
    assert res.docx_template_file_id == 2
