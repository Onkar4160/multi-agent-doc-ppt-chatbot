"""Tests verifying absence of DetachedInstanceError and proper session lifecycle across nodes."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.agents.converter import ConvertResult, convert_artifact
from app.agents.editor import EditResult, edit_artifact
from app.agents.graph import graph_app, node_edit, node_finalize
from app.agents.state import GraphState
from app.core.database import Base, run_migrations_sync
from app.models.artifact import Artifact, ArtifactVersion
from app.models.deck_model import BulletItem, DeckModel, SlideModel
from app.models.document_model import DocumentModel, ParagraphBlock, Section
from app.models.file import UploadedFile
from app.models.workspace import Workspace


@pytest.fixture
def fresh_engine():
    """Create isolated SQLite engine with tables and workspace setup."""
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        run_migrations_sync(conn)

    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    db = Session()

    out_dir = Path("data/outputs")
    out_dir.mkdir(parents=True, exist_ok=True)
    tmpl_docx = Path("data/sample_templates/proposal_Template.docx")
    tmpl_pptx = Path("data/sample_templates/presentation_Template.pptx")
    tmpl_docx.parent.mkdir(parents=True, exist_ok=True)
    if not tmpl_docx.exists():
        from docx import Document
        d = Document()
        d.save(tmpl_docx)
    if not tmpl_pptx.exists():
        from pptx import Presentation
        p = Presentation()
        p.save(tmpl_pptx)

    f_doc = UploadedFile(filename="test.docx", file_type="docx", stored_path=str(tmpl_docx), file_size=100)
    f_ppt = UploadedFile(filename="test.pptx", file_type="pptx", stored_path=str(tmpl_pptx), file_size=100)
    db.add(f_doc)
    db.add(f_ppt)
    db.flush()

    ws = Workspace(
        name="Default Workspace",
        is_active=True,
        docx_template_file_id=f_doc.id,
        pptx_template_file_id=f_ppt.id,
    )
    db.add(ws)
    db.commit()
    db.close()
    return engine


def test_edit_result_and_convert_result_plain_values_only():
    """Test (b): EditResult and ConvertResult contain plain values only, no ORM instances."""
    er = EditResult(
        artifact_id=1,
        new_version_no=2,
        summary="Added pricing section",
        diff={"added": ["Pricing"]},
        file_path="/path/to/v2.docx",
        download_url="/artifacts/1/download?version=2",
        llm_calls=2,
    )
    for field_name, val in er.model_dump().items():
        assert not hasattr(val, "_sa_instance_state"), f"EditResult.{field_name} must not be an ORM instance"

    cr = ConvertResult(
        new_artifact_id=2,
        target_kind="pptx",
        title="Converted Presentation",
        version_no=1,
        file_path="/path/to/v1.pptx",
        download_url="/artifacts/2/download?version=1",
    )
    for field_name, val in cr.model_dump().items():
        assert not hasattr(val, "_sa_instance_state"), f"ConvertResult.{field_name} must not be an ORM instance"


def test_failure_before_commit_leaves_no_new_version(fresh_engine):
    """Test (c): A failure before commit leaves no new version."""
    Session = sessionmaker(bind=fresh_engine, autoflush=False, autocommit=False, expire_on_commit=False)
    db = Session()

    doc = DocumentModel(
        title="Doc To Fail",
        sections=[Section(heading="Intro", level=1, blocks=[ParagraphBlock(text="Hello", source_ids=[1])])],
    )
    art = Artifact(title=doc.title, artifact_type="docx", session_id="fail_sess", run_id="r0")
    db.add(art)
    db.flush()
    ver = ArtifactVersion(
        artifact_id=art.id,
        version_no=1,
        model_json=doc.model_dump_json(),
        file_path="fake.docx",
        file_type="docx",
    )
    db.add(ver)
    db.commit()
    art_id = art.id
    db.close()

    # Edit with mock failure during render
    db_edit = Session()
    with patch("app.agents.editor.render_docx", side_effect=RuntimeError("Simulated render failure")):
        with pytest.raises(RuntimeError, match="Simulated render failure"):
            edit_artifact(artifact_id=art_id, instruction="add more text", db_session=db_edit)

    db_edit.close()

    # Verify no version 2 was committed
    db_verify = Session()
    versions = db_verify.query(ArtifactVersion).filter(ArtifactVersion.artifact_id == art_id).all()
    assert len(versions) == 1
    assert versions[0].version_no == 1
    db_verify.close()


def test_consecutive_edits_with_new_session_per_node(fresh_engine):
    """Test (a): Use a NEW session per node call, closed after each node.
    Run: generate -> edit 'add a section on X' -> edit 'add an executive summary'.
    Assert: no DetachedInstanceError, versions v2 and v3 exist, same artifact ids, reply starts with 'Edited: ...', diff present.
    """
    Session = sessionmaker(bind=fresh_engine, autoflush=False, autocommit=False, expire_on_commit=False)

    def session_factory():
        # Returns a fresh new session every single time it is called
        return Session()

    session_id = "test_detached_inst_session"

    # Seed initial generated document and presentation
    db = Session()
    doc = DocumentModel(
        title="Enterprise AI Overview",
        subtitle="Strategy Document",
        sections=[
            Section(heading="1. Overview", level=1, blocks=[ParagraphBlock(text="Intro text", source_ids=[1])]),
        ],
    )
    deck = DeckModel(
        title="Enterprise AI Deck",
        slides=[
            SlideModel(role="title", title="Enterprise AI Deck", subtitle="Subtitle", source_ids=[1]),
            SlideModel(role="title_content", title="Overview", bullets=[BulletItem(text="Bullet 1", level=0, source_ids=[1])], source_ids=[1]),
        ],
    )
    art_doc = Artifact(title=doc.title, artifact_type="docx", session_id=session_id, run_id="run_gen")
    art_deck = Artifact(title=deck.title, artifact_type="pptx", session_id=session_id, run_id="run_gen")
    db.add(art_doc)
    db.add(art_deck)
    db.flush()

    v_doc = ArtifactVersion(
        artifact_id=art_doc.id,
        version_no=1,
        model_json=doc.model_dump_json(),
        file_path="Proposal_v1.docx",
        file_type="docx",
        source_ids_json=json.dumps([1]),
    )
    v_deck = ArtifactVersion(
        artifact_id=art_deck.id,
        version_no=1,
        model_json=deck.model_dump_json(),
        file_path="Deck_v1.pptx",
        file_type="pptx",
        source_ids_json=json.dumps([1]),
    )
    db.add(v_doc)
    db.add(v_deck)
    db.commit()
    doc_id = art_doc.id
    deck_id = art_deck.id
    db.close()

    # Turn 2: Edit "add a section on X"
    state_edit1: GraphState = {
        "run_id": "run_edit_1",
        "session_id": session_id,
        "user_message": "add a section on security and compliance",
        "plan": {
            "action": "edit",
            "resolved_message": "add a section on security and compliance",
            "target_artifact_ids": [doc_id],
        },
    }

    with patch("app.agents.graph._get_sync_session", side_effect=session_factory):
        with patch("app.agents.editor.render_docx"):
            with patch("app.agents.editor.render_pptx"):
                res1 = node_edit(state_edit1)

    assert "reply" in res1
    assert res1["reply"].startswith("Edited: Enterprise AI Overview")
    assert "v1 -> v2" in res1["reply"]
    assert "diff" in res1["artifacts"][0]
    assert res1["artifacts"][0]["version"] == 2
    assert res1["artifacts"][0]["artifact_id"] == doc_id

    # Verify in DB: exactly 2 versions exist for art_doc
    db2 = Session()
    doc_vers_after_edit1 = db2.query(ArtifactVersion).filter(ArtifactVersion.artifact_id == doc_id).all()
    assert len(doc_vers_after_edit1) == 2
    db2.close()

    # Turn 3: Edit "Add an executive summary"
    state_edit2: GraphState = {
        "run_id": "run_edit_2",
        "session_id": session_id,
        "user_message": "Add an executive summary",
        "plan": {
            "action": "edit",
            "resolved_message": "Add an executive summary",
            "target_artifact_ids": [doc_id],
        },
    }

    with patch("app.agents.graph._get_sync_session", side_effect=session_factory):
        with patch("app.agents.editor.render_docx"):
            with patch("app.agents.editor.render_pptx"):
                res2 = node_edit(state_edit2)

    assert "reply" in res2
    assert res2["reply"].startswith("Edited: Enterprise AI Overview")
    assert "v2 -> v3" in res2["reply"]
    assert "diff" in res2["artifacts"][0]
    assert res2["artifacts"][0]["version"] == 3
    assert res2["artifacts"][0]["artifact_id"] == doc_id

    # Verify in DB: exactly 3 versions exist for art_doc
    db3 = Session()
    doc_vers_after_edit2 = db3.query(ArtifactVersion).filter(ArtifactVersion.artifact_id == doc_id).order_by(ArtifactVersion.version_no.asc()).all()
    assert len(doc_vers_after_edit2) == 3
    assert doc_vers_after_edit2[0].version_no == 1
    assert doc_vers_after_edit2[1].version_no == 2
    assert doc_vers_after_edit2[2].version_no == 3
    db3.close()
