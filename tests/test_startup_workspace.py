"""Tests for startup workspace initialization and template resolution without per-request file_ids."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.agents.converter import convert_artifact
from app.agents.editor import edit_artifact
from app.agents.graph import graph_app
from app.agents.state import GraphState
from app.core.config import get_settings
from app.core.database import Base
from app.models.artifact import Artifact, ArtifactVersion
from app.models.file import UploadedFile
from app.models.workspace import Workspace
from app.services.workspace_init import init_default_workspace_sync


@pytest.fixture
def sync_db_session():
    """Create an isolated in-memory SQLite session with tables."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    yield session
    session.close()


def test_startup_creates_exactly_one_active_workspace(sync_db_session):
    """Startup creates exactly one active workspace from the configured template paths, idempotently."""
    settings = get_settings()

    # 1. Run startup initialization on clean DB
    ws = init_default_workspace_sync(sync_db_session)

    assert ws is not None
    assert ws.is_active is True

    # Exactly 1 active workspace exists
    active_count = sync_db_session.query(Workspace).filter(Workspace.is_active == True).count()
    assert active_count == 1

    # Check template files were registered
    doc_f = sync_db_session.query(UploadedFile).filter(UploadedFile.id == ws.docx_template_file_id).first()
    ppt_f = sync_db_session.query(UploadedFile).filter(UploadedFile.id == ws.pptx_template_file_id).first()
    assert doc_f is not None
    assert ppt_f is not None
    assert doc_f.filename == Path(settings.default_docx_template_path).name
    assert ppt_f.filename == Path(settings.default_pptx_template_path).name

    # Check profiles were analyzed and stored
    assert ws.doc_profile_json is not None
    assert ws.ppt_profile_json is not None
    doc_prof = json.loads(ws.doc_profile_json)
    ppt_prof = json.loads(ws.ppt_profile_json)
    assert "source_name" in doc_prof
    assert "source_name" in ppt_prof

    # 2. Run startup initialization a second time (should be idempotent, no duplicates)
    ws_second = init_default_workspace_sync(sync_db_session)
    assert ws_second.id == ws.id
    total_count = sync_db_session.query(Workspace).count()
    assert total_count == 1
    active_count = sync_db_session.query(Workspace).filter(Workspace.is_active == True).count()
    assert active_count == 1


def test_missing_configured_docx_file_fails_startup_with_clear_message(sync_db_session):
    """Missing configured DOCX template fails startup with a clear error naming the missing path."""
    fake_path = "non_existent/custom_templates/missing_proposal.docx"
    with patch.object(get_settings(), "default_docx_template_path", fake_path):
        with pytest.raises(FileNotFoundError) as exc_info:
            init_default_workspace_sync(sync_db_session)

        err_msg = str(exc_info.value).replace("\\", "/")
        assert fake_path in err_msg
        assert "DEFAULT_DOCX_TEMPLATE_PATH" in err_msg


def test_missing_configured_pptx_file_fails_startup_with_clear_message(sync_db_session):
    """Missing configured PPTX template fails startup with a clear error naming the missing path."""
    fake_path = "non_existent/custom_templates/missing_deck.pptx"
    with patch.object(get_settings(), "default_pptx_template_path", fake_path):
        with pytest.raises(FileNotFoundError) as exc_info:
            init_default_workspace_sync(sync_db_session)

        err_msg = str(exc_info.value).replace("\\", "/")
        assert fake_path in err_msg
        assert "DEFAULT_PPTX_TEMPLATE_PATH" in err_msg


def test_generate_edit_convert_using_fixed_workspace_no_file_id(sync_db_session):
    """End-to-end test: generate, edit, and convert all succeed using the fixed workspace without file_ids."""
    # 1. Initialize active workspace at startup
    ws = init_default_workspace_sync(sync_db_session)
    assert ws.is_active is True

    session_factory = sessionmaker(bind=sync_db_session.get_bind(), expire_on_commit=False)

    # 2. GENERATE: invoke graph pipeline with NO file_ids
    with patch("app.agents.graph._get_sync_session", side_effect=session_factory):
        initial_state: GraphState = {
            "run_id": "test-run-1",
            "session_id": "test-sess-1",
            "user_message": "Create a brief proposal on cloud security (3 slides)",
            "file_ids": [],  # No file_ids in request!
            "artifacts": [],
            "errors": [],
            "retry_count": 0,
            "findings": [],
            "template_profiles": {},
        }

        final_state = graph_app.invoke(initial_state)

        artifacts = final_state.get("artifacts", [])
        assert len(artifacts) >= 1
        docx_art = next((a for a in artifacts if a.get("kind") == "docx"), None)
        assert docx_art is not None, "DOCX artifact was not generated"
        assert Path(docx_art["file_path"]).exists()

        # Check DB artifact versions were saved
        with session_factory() as sess:
            docx_ver = sess.query(ArtifactVersion).filter(ArtifactVersion.artifact_id == docx_art["artifact_id"]).first()
            assert docx_ver is not None
            assert docx_ver.file_type == "docx"

        # 3. EDIT: edit the generated DOCX artifact without file_id
        edit_res = edit_artifact(
            artifact_id=docx_art["artifact_id"],
            instruction="add a short conclusion section",
            db_session=sync_db_session,
        )
        assert edit_res.new_version_no == 2
        assert Path(edit_res.file_path).exists()

        # 4. CONVERT: convert DOCX artifact to PPTX without file_id
        convert_res = convert_artifact(
            artifact_id=docx_art["artifact_id"],
            target_kind="pptx",
            db_session=sync_db_session,
        )
        assert convert_res.target_kind == "pptx"
        assert Path(convert_res.file_path).exists()
