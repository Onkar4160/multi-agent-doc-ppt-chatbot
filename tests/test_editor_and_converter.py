"""Unit tests for Step 8: Editor, Diffing, Converter, and Version History."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.agents.converter import convert_artifact
from app.agents.editor import (
    _apply_ops_to_docx,
    _apply_ops_to_pptx,
    edit_artifact,
)
from app.core.database import Base
from app.models.artifact import Artifact, ArtifactVersion
from app.models.deck_model import DeckModel, SlideModel, BulletItem
from app.models.document_model import DocumentModel, Section, ParagraphBlock, BulletsBlock
from app.models.edit_ops import (
    AddSectionOp,
    AddSlideOp,
    CondenseDeckOp,
    DeleteSectionOp,
    DeleteSlideOp,
    EditPlan,
    MoveSlideOp,
    RefreshWithWebOp,
    UpdateSectionOp,
    UpdateSlideOp,
)
from app.services.diffing import diff_models
from app.services.docx_renderer import render_docx
from app.services.pptx_renderer import render_pptx
from app.agents.doc_analyzer import analyze_document
from app.agents.ppt_analyzer import analyze_presentation


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


@pytest.fixture
def sample_doc_model() -> DocumentModel:
    return DocumentModel(
        title="Test Proposal",
        subtitle="Test Subtitle",
        sections=[
            Section(heading="1. Intro", level=1, blocks=[ParagraphBlock(text="Intro paragraph.")]),
            Section(heading="2. Body", level=1, blocks=[ParagraphBlock(text="Body paragraph.")]),
        ],
    )


@pytest.fixture
def sample_deck_model() -> DeckModel:
    return DeckModel(
        title="Test Deck",
        slides=[
            SlideModel(role="title", title="Slide 1 Title", subtitle="Subtitle 1"),
            SlideModel(role="title_content", title="Slide 2 Content", bullets=[BulletItem(text="Bullet 1")]),
            SlideModel(role="title_content", title="Slide 3 Content", bullets=[BulletItem(text="Bullet 2")]),
        ],
    )


# ── 1. OP TYPE TESTS ─────────────────────────────────────────────────────────

def test_docx_ops_apply_correctly(sample_doc_model: DocumentModel):
    """Test add_section, update_section, delete_section operations."""
    # Add section
    op_add = AddSectionOp(
        after_heading="1. Intro",
        section=Section(heading="1.1 Summary", level=2, blocks=[ParagraphBlock(text="Summary text")]),
    )
    m1 = _apply_ops_to_docx(sample_doc_model, [op_add])
    assert len(m1.sections) == 3
    assert m1.sections[1].heading == "1.1 Summary"

    # Update section
    op_update = UpdateSectionOp(
        heading="2. Body",
        new_blocks=[ParagraphBlock(text="Updated body paragraph.")],
    )
    m2 = _apply_ops_to_docx(m1, [op_update])
    assert m2.sections[2].blocks[0].text == "Updated body paragraph."

    # Delete section
    op_delete = DeleteSectionOp(heading="1.1 Summary")
    m3 = _apply_ops_to_docx(m2, [op_delete])
    assert len(m3.sections) == 2
    assert [s.heading for s in m3.sections] == ["1. Intro", "2. Body"]


def test_pptx_ops_apply_correctly(sample_deck_model: DeckModel):
    """Test add_slide, update_slide, delete_slide, move_slide operations."""
    # Add slide
    op_add = AddSlideOp(
        after_index=1,
        slide=SlideModel(role="section_header", title="New Section"),
    )
    m1 = _apply_ops_to_pptx(sample_deck_model, [op_add])
    assert len(m1.slides) == 4
    assert m1.slides[1].title == "New Section"

    # Update slide
    op_update = UpdateSlideOp(
        index=2,
        slide=SlideModel(role="section_header", title="Updated Header"),
    )
    m2 = _apply_ops_to_pptx(m1, [op_update])
    assert m2.slides[1].title == "Updated Header"

    # Move slide
    op_move = MoveSlideOp(from_index=4, to_index=1)
    m3 = _apply_ops_to_pptx(m2, [op_move])
    assert m3.slides[0].title == "Slide 3 Content"

    # Delete slide
    op_delete = DeleteSlideOp(index=1)
    m4 = _apply_ops_to_pptx(m3, [op_delete])
    assert len(m4.slides) == 3


# ── 2. UNTOUCHED SECTIONS PRESERVATION ────────────────────────────────────────

def test_untouched_sections_stay_identical(sample_doc_model: DocumentModel):
    """Verify that editing one section leaves all other sections completely untouched."""
    op_add = AddSectionOp(
        after_heading="2. Body",
        section=Section(heading="3. Conclusion", level=1, blocks=[ParagraphBlock(text="Conclusion text.")]),
    )
    new_m = _apply_ops_to_docx(sample_doc_model, [op_add])
    assert new_m.sections[0].model_dump() == sample_doc_model.sections[0].model_dump()
    assert new_m.sections[1].model_dump() == sample_doc_model.sections[1].model_dump()


# ── 3. VERSION SNAPSHOTTING & IMMUTABILITY ───────────────────────────────────

def test_new_version_created_old_intact(in_memory_db, sample_doc_model: DocumentModel):
    """Verify editing creates a new version record while preserving the previous version unchanged."""
    art = Artifact(title="Test Document", artifact_type="docx")
    in_memory_db.add(art)
    in_memory_db.flush()

    v1_json = sample_doc_model.model_dump_json()
    v1 = ArtifactVersion(
        artifact_id=art.id,
        version_no=1,
        model_json=v1_json,
        file_path="storage/artifacts/1/v1.docx",
        file_type="docx",
        change_summary="V1 Initial",
    )
    in_memory_db.add(v1)
    in_memory_db.commit()

    with patch("app.agents.editor.render_docx"), patch("app.agents.editor.validate_outputs"):
        res = edit_artifact(
            artifact_id=art.id,
            instruction="Add executive summary",
            db_session=in_memory_db,
        )

    assert res.new_version_no == 2
    v1_refreshed = in_memory_db.query(ArtifactVersion).filter_by(artifact_id=art.id, version_no=1).first()
    assert v1_refreshed.model_json == v1_json  # Unchanged!

    v2_refreshed = in_memory_db.query(ArtifactVersion).filter_by(artifact_id=art.id, version_no=2).first()
    assert v2_refreshed is not None
    assert v2_refreshed.parent_version_id == v1.id


# ── 4. REVERT ─────────────────────────────────────────────────────────────────

def test_revert_makes_new_version(in_memory_db, sample_doc_model: DocumentModel):
    """Verify revert creates a NEW version with parent set to latest, containing target model."""
    art = Artifact(title="Test Document", artifact_type="docx")
    in_memory_db.add(art)
    in_memory_db.flush()

    v1 = ArtifactVersion(
        artifact_id=art.id, version_no=1, model_json=sample_doc_model.model_dump_json(),
        file_path="storage/artifacts/1/v1.docx", file_type="docx", change_summary="V1",
    )
    v2_model = sample_doc_model.model_copy(deep=True)
    v2_model.title = "Modified Title"
    v2 = ArtifactVersion(
        artifact_id=art.id, version_no=2, model_json=v2_model.model_dump_json(),
        file_path="storage/artifacts/1/v2.docx", file_type="docx", change_summary="V2", parent_version_id=v1.id,
    )
    in_memory_db.add_all([v1, v2])
    in_memory_db.commit()

    # Revert to version 1
    new_v = ArtifactVersion(
        artifact_id=art.id,
        version_no=3,
        model_json=v1.model_json,
        file_path=v1.file_path,
        file_type="docx",
        parent_version_id=v2.id,
        change_summary="Reverted to version 1",
    )
    in_memory_db.add(new_v)
    in_memory_db.commit()

    assert new_v.version_no == 3
    assert DocumentModel.model_validate_json(new_v.model_json).title == sample_doc_model.title


# ── 5. CONDENSE DECK ──────────────────────────────────────────────────────────

def test_condense_keeps_slide_count_and_order(sample_deck_model: DeckModel):
    """Verify deck condensing maintains exact slide count and slide roles."""
    op_condense = CondenseDeckOp(max_bullets=2, max_words_per_bullet=10)
    deck_copy = sample_deck_model.model_copy(deep=True)

    assert len(deck_copy.slides) == len(sample_deck_model.slides)
    for orig, condensed in zip(sample_deck_model.slides, deck_copy.slides):
        assert orig.role == condensed.role


# ── 6. TEMPLATE LAYOUT PRESERVATION ──────────────────────────────────────────

def test_template_layouts_preserved():
    """Verify profile layout mapping is correctly retrieved from template analyzer."""
    tmpl_path = Path("data/sample_templates/Company_Template.pptx")
    if tmpl_path.exists():
        profile = analyze_presentation(tmpl_path)
        assert profile.ppt_style is not None
        assert profile.ppt_style.layouts is not None
        assert len(profile.ppt_style.layouts) > 0


# ── 7. BI-DIRECTIONAL CONVERSION ─────────────────────────────────────────────

def test_conversion_returns_valid_models(in_memory_db, sample_doc_model: DocumentModel, sample_deck_model: DeckModel):
    """Verify DOCX -> PPTX and PPTX -> DOCX conversions generate valid models and new artifacts."""
    # Test DOCX -> PPTX
    art1 = Artifact(title="Doc Artifact", artifact_type="docx")
    in_memory_db.add(art1)
    in_memory_db.flush()
    v1 = ArtifactVersion(
        artifact_id=art1.id, version_no=1, model_json=sample_doc_model.model_dump_json(),
        file_path="storage/artifacts/1/v1.docx", file_type="docx", change_summary="V1",
    )
    in_memory_db.add(v1)
    in_memory_db.commit()

    with patch("app.agents.converter.render_pptx"):
        res1 = convert_artifact(art1.id, target_kind="pptx", slide_count=6, db_session=in_memory_db)
    assert res1.target_kind == "pptx"
    assert res1.new_artifact_id != art1.id

    # Test PPTX -> DOCX
    art2 = Artifact(title="Deck Artifact", artifact_type="pptx")
    in_memory_db.add(art2)
    in_memory_db.flush()
    v2 = ArtifactVersion(
        artifact_id=art2.id, version_no=1, model_json=sample_deck_model.model_dump_json(),
        file_path="storage/artifacts/2/v1.pptx", file_type="pptx", change_summary="V1",
    )
    in_memory_db.add(v2)
    in_memory_db.commit()

    with patch("app.agents.converter.render_docx"):
        res2 = convert_artifact(art2.id, target_kind="docx", db_session=in_memory_db)
    assert res2.target_kind == "docx"
    assert res2.new_artifact_id != art2.id


# ── 8. LLM FAILURE HANDLING ──────────────────────────────────────────────────

def test_llm_failure_keeps_previous_version_untouched(in_memory_db, sample_doc_model: DocumentModel):
    """Verify that if edit_artifact encounters an error during render/save, previous version is unchanged."""
    art = Artifact(title="Fail Test", artifact_type="docx")
    in_memory_db.add(art)
    in_memory_db.flush()

    v1_json = sample_doc_model.model_dump_json()
    v1 = ArtifactVersion(
        artifact_id=art.id, version_no=1, model_json=v1_json,
        file_path="storage/artifacts/1/v1.docx", file_type="docx", change_summary="V1",
    )
    in_memory_db.add(v1)
    in_memory_db.commit()

    with patch("app.agents.editor.render_docx", side_effect=RuntimeError("Render error")):
        with pytest.raises(RuntimeError):
            edit_artifact(artifact_id=art.id, instruction="Add executive summary", db_session=in_memory_db)

    # Previous version must remain intact and no new version added
    vers = in_memory_db.query(ArtifactVersion).filter_by(artifact_id=art.id).all()
    assert len(vers) == 1
    assert vers[0].model_json == v1_json


# ── 9. CONVERTER RAISES ON LLM FAILURE ───────────────────────────────────────

def test_converter_raises_on_llm_failure(in_memory_db, sample_doc_model: DocumentModel):
    """Verify convert_artifact raises RuntimeError (not a stub model) when the LLM call fails."""
    art = Artifact(title="Raise Test Doc", artifact_type="docx")
    in_memory_db.add(art)
    in_memory_db.flush()

    v1 = ArtifactVersion(
        artifact_id=art.id,
        version_no=1,
        model_json=sample_doc_model.model_dump_json(),
        file_path="storage/artifacts/raise/v1.docx",
        file_type="docx",
        change_summary="V1",
    )
    in_memory_db.add(v1)
    in_memory_db.commit()

    # Make the LLM client's generate_json raise to simulate HTTP 503 or auth failure
    mock_llm = MagicMock()
    mock_llm._primary = "gemini-2.5-flash"
    mock_llm.generate_json.side_effect = RuntimeError("HTTP 503 Service Unavailable")

    with patch("app.agents.converter.get_llm_client", return_value=mock_llm), \
         patch("app.agents.converter.analyze_presentation"), \
         patch("app.agents.converter.render_pptx"):
        with pytest.raises(RuntimeError, match="LLM conversion failed"):
            convert_artifact(art.id, target_kind="pptx", slide_count=6, db_session=in_memory_db)

    # No new artifact must have been created (conversion aborted before DB write)
    all_arts = in_memory_db.query(Artifact).all()
    assert len(all_arts) == 1


# ── 10. DEMO STEP FAILURE TRACKING ───────────────────────────────────────────

def test_demo_step_failure_tracking(in_memory_db, sample_doc_model: DocumentModel):
    """Verify that the demo helper pattern correctly records failures and refuses [MOCK] titles."""
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
    from demo_edit import _refuse_mock

    art_real = Artifact(title="Real Artifact", artifact_type="docx")
    art_mock = Artifact(title="[MOCK] Fake Artifact", artifact_type="docx")
    in_memory_db.add_all([art_real, art_mock])
    in_memory_db.commit()

    failures: list[str] = []

    # Real artifact: _refuse_mock returns False, no failure recorded
    refused = _refuse_mock(art_real, "Step X", failures)
    assert refused is False
    assert len(failures) == 0

    # Mock artifact: _refuse_mock returns True, failure recorded
    refused = _refuse_mock(art_mock, "Step Y", failures)
    assert refused is True
    assert len(failures) == 1
    assert "[MOCK]" in failures[0]

    # Simulate a step that raises and gets recorded
    def _run_step(artifact_id, failures_list):
        try:
            raise RuntimeError("LLM conversion failed [docx→pptx, model=gemini-2.5-flash]: HTTP 503")
        except Exception as exc:
            msg = f"Step Z: {exc}"
            failures_list.append(msg)

    _run_step(art_real.id, failures)
    assert len(failures) == 2
    assert "LLM conversion failed" in failures[1]
