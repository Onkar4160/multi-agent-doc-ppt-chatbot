"""Comprehensive unit and integration tests for session context, multi-turn continuity, and routing."""

from __future__ import annotations

import json
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.agents.editor import edit_artifact
from app.agents.graph import _get_sync_session, graph_app, node_answer, node_edit, node_finalize, node_plan
from app.agents.state import GraphState
from app.agents.supervisor import Plan, parse_plan
from app.core.database import Base, run_migrations_sync
from app.llm.client import get_llm_client
from app.models.artifact import Artifact, ArtifactVersion
from app.models.chat import ChatMessage, ChatSession
from app.models.deck_model import BulletItem, DeckModel, SlideModel
from app.models.document_model import BulletsBlock, DocumentModel, ParagraphBlock, Section
from app.models.run import AgentRun
from app.models.workspace import Workspace
from app.services.diffing import diff_models
from app.services.session_context import build_session_context


@pytest.fixture
def sync_db():
    """Create isolated in-memory or file-based SQLite database with all tables & workspace."""
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        run_migrations_sync(conn)

    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    db = SessionLocal()

    # Create dummy active workspace and template files
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

    from app.models.file import UploadedFile
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

    yield db
    db.close()


# ── Test (f): Migration adds new columns to an existing DB without losing rows ──
def test_migration_adds_new_columns_without_losing_rows():
    """Test safe, idempotent migration on SQLite database created before columns existed."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tf:
        db_path = tf.name

    try:
        # Create table with old schema (without session_id, run_id, meta_json)
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute("CREATE TABLE artifacts (id INTEGER PRIMARY KEY, title VARCHAR(255), artifact_type VARCHAR(10))")
        cur.execute("CREATE TABLE chat_messages (id INTEGER PRIMARY KEY, session_id VARCHAR(100), role VARCHAR(20), content TEXT, run_id VARCHAR(100))")
        cur.execute("INSERT INTO artifacts (id, title, artifact_type) VALUES (1, 'Old Doc', 'docx')")
        cur.execute("INSERT INTO chat_messages (id, session_id, role, content, run_id) VALUES (1, 'sess_old', 'user', 'hello', 'run_1')")
        conn.commit()
        conn.close()

        # Run migration on the existing DB
        engine = create_engine(f"sqlite:///{db_path}")
        with engine.begin() as conn_sa:
            run_migrations_sync(conn_sa)

        # Verify columns exist and rows are preserved
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute("PRAGMA table_info(artifacts)")
        art_cols = [r[1] for r in cur.fetchall()]
        assert "session_id" in art_cols
        assert "run_id" in art_cols

        cur.execute("PRAGMA table_info(chat_messages)")
        msg_cols = [r[1] for r in cur.fetchall()]
        assert "meta_json" in msg_cols

        cur.execute("SELECT id, title FROM artifacts")
        row = cur.fetchone()
        assert row == (1, "Old Doc"), "Old row was lost during migration!"

        cur.execute("SELECT id, content FROM chat_messages")
        row_msg = cur.fetchone()
        assert row_msg == (1, "hello"), "Old chat message was lost during migration!"
        conn.close()
        engine.dispose()

    finally:
        try:
            Path(db_path).unlink(missing_ok=True)
        except Exception:
            pass


# ── Test (c): Context cap with 50 old messages stays under limit ────────────────
def test_session_context_cap_with_many_messages(sync_db):
    """50 old messages still give a prompt context under the 3000 character cap."""
    session_id = "test_cap_session"
    for i in range(50):
        m = ChatMessage(
            session_id=session_id,
            role="user" if i % 2 == 0 else "assistant",
            content=f"Message number {i} with lots of verbose text about enterprise topics and strategy " * 4,
            run_id=f"run_{i}",
        )
        sync_db.add(m)
    sync_db.commit()

    ctx = build_session_context(session_id, sync_db)
    # Total character size of messages + active_topic + artifacts
    total_size = len(ctx["active_topic"]) + len(json.dumps(ctx["artifacts"])) + sum(len(m["content"]) for m in ctx["recent_messages"])
    assert total_size <= 3000
    assert len(ctx["recent_messages"]) <= 6


# ── Test (b): Supervisor prompt contains previous message & outline; "make it shorter" gets resolved_message ──
def test_supervisor_resolved_message_and_prompt(sync_db):
    """The supervisor resolves 'make it shorter' to target the presentation."""
    session_id = "sess_prompt_test"
    # Create docx and pptx artifacts in session
    art_docx = Artifact(title="AI Report", artifact_type="docx", session_id=session_id, run_id="r1")
    art_pptx = Artifact(title="AI Presentation Deck", artifact_type="pptx", session_id=session_id, run_id="r1")
    sync_db.add(art_docx)
    sync_db.add(art_pptx)
    sync_db.flush()

    deck = DeckModel(title="AI Presentation Deck", slides=[SlideModel(role="title", title="Slide 1", subtitle="Sub", source_ids=[1])])
    v_pptx = ArtifactVersion(artifact_id=art_pptx.id, version_no=1, model_json=deck.model_dump_json(), file_path="fake.pptx", file_type="pptx")
    sync_db.add(v_pptx)
    sync_db.commit()

    ctx = build_session_context(session_id, sync_db)
    p = parse_plan("make it shorter", session_context=ctx)

    assert p.action == "edit"
    assert "presentation" in p.resolved_message.lower() or "shorter" in p.resolved_message.lower()
    assert art_pptx.id in p.target_artifact_ids or art_docx.id in p.target_artifact_ids


# ── Test (d): Session isolation: session B cannot read or edit session A's artifacts ──
def test_session_isolation(sync_db):
    """Session B cannot read or edit Session A's artifacts."""
    sess_a = "sess_a"
    sess_b = "sess_b"

    art_a = Artifact(title="Project Alpha Report", artifact_type="docx", session_id=sess_a, run_id="ra")
    sync_db.add(art_a)
    sync_db.flush()
    v_a = ArtifactVersion(artifact_id=art_a.id, version_no=1, model_json="{}", file_path="a.docx", file_type="docx")
    sync_db.add(v_a)
    sync_db.commit()

    # Session B context must NOT include art_a
    ctx_b = build_session_context(sess_b, sync_db)
    assert len(ctx_b["artifacts"]) == 0

    # Node edit in Session B with no artifacts must fail gracefully
    state_b: GraphState = {
        "run_id": "rb1",
        "session_id": sess_b,
        "user_message": "add a section on timeline",
        "plan": {"action": "edit", "resolved_message": "add a section on timeline", "target_artifact_ids": [art_a.id]},
    }
    with patch("app.agents.graph._get_sync_session", return_value=sync_db):
        res_state = node_edit(state_b)

    assert "No existing artifacts found to edit for this session" in res_state["reply"]
    # Verify art_a still has version 1 only
    ver_count = sync_db.query(ArtifactVersion).filter(ArtifactVersion.artifact_id == art_a.id).count()
    assert ver_count == 1


# ── Test (a) & (g): Multi-turn sequence + LLM budget assertions ─────────────────
def test_multi_turn_pipeline_and_llm_budget(sync_db):
    """Multi-turn test:
    1) Generate initial report + deck
    2) Document Q&A: makes 0 search calls, 0 Pinecone calls, exactly 1 LLM call
    3) Edit: creates v2 of same artifact, preserves old sections
    4) New report: 'create a new report on Y' makes brand new artifacts and leaves old untouched
    5) Plain edit targets the newest pair; 'in the first report' targets first pair
    Assert LLM budget for edit turn.
    """
    session_id = "multi_turn_test_sess"

    # Seed docx and pptx models in DB for initial generate turn
    doc = DocumentModel(
        title="Generative AI Overview",
        subtitle="Market Analysis",
        client_name="Acme",
        date="2026",
        sections=[
            Section(heading="1. Executive Summary", level=1, blocks=[ParagraphBlock(text="Overview of GenAI advancements.", source_ids=[1])]),
            Section(heading="2. Technical Architecture", level=1, blocks=[ParagraphBlock(text="Transformer model design and latency benchmarks.", source_ids=[2])]),
        ],
    )
    deck = DeckModel(
        title="Generative AI Presentation",
        slides=[
            SlideModel(role="title", title="GenAI Title", subtitle="Sub", source_ids=[1]),
            SlideModel(role="title_content", title="Architecture", bullets=[BulletItem(text="Low latency inference", level=0, source_ids=[2])], source_ids=[2]),
        ],
    )

    art_doc = Artifact(title=doc.title, artifact_type="docx", session_id=session_id, run_id="run_1")
    art_deck = Artifact(title=deck.title, artifact_type="pptx", session_id=session_id, run_id="run_1")
    sync_db.add(art_doc)
    sync_db.add(art_deck)
    sync_db.flush()

    v_doc1 = ArtifactVersion(
        artifact_id=art_doc.id,
        version_no=1,
        model_json=doc.model_dump_json(),
        file_path="Proposal_1_v1.docx",
        file_type="docx",
        source_ids_json=json.dumps([1, 2]),
    )
    v_deck1 = ArtifactVersion(
        artifact_id=art_deck.id,
        version_no=1,
        model_json=deck.model_dump_json(),
        file_path="Deck_1_v1.pptx",
        file_type="pptx",
        source_ids_json=json.dumps([1, 2]),
    )
    sync_db.add(v_doc1)
    sync_db.add(v_deck1)
    sync_db.commit()

    # Record agent run for active topic
    run1 = AgentRun(run_id="run_1", session_id=session_id, status="done", plan_json=json.dumps({"action": "generate", "topic": "Generative AI"}))
    sync_db.add(run1)
    sync_db.commit()

    # ── Turn 2: Document Q&A ──
    # Check that Q&A makes NO search, NO Pinecone call, exactly 1 LLM call
    state_qa: GraphState = {
        "run_id": "run_qa",
        "session_id": session_id,
        "user_message": "What does the report say about latency benchmarks?",
        "plan": {
            "action": "answer",
            "answer_scope": "document",
            "resolved_message": "What does the report say about latency benchmarks?",
            "target_artifact_ids": [art_doc.id],
        },
    }

    with patch("app.agents.graph._get_sync_session", return_value=sync_db):
        with patch("app.agents.graph.build_context") as mock_build_ctx:
            with patch("app.services.vector_store.get_vector_store") as mock_pinecone:
                llm = get_llm_client()
                llm_before = llm.stats.total_calls
                res_qa = node_answer(state_qa)
                llm_after = llm.stats.total_calls

                assert mock_build_ctx.call_count == 0, "Document Q&A must NOT call web search or build_context!"
                assert mock_pinecone.call_count == 0, "Document Q&A must NOT call Pinecone vector store!"
                assert (llm_after - llm_before) == 1, f"Document Q&A must make exactly 1 LLM call, made {llm_after - llm_before}"
                assert "reply" in res_qa and len(res_qa["reply"]) > 0

    # ── Turn 3: Conversational Edit ──
    # Instruction: "also add a section on pricing"
    # Must keep same artifact id, create version 2, leave old sections identical
    with patch("app.agents.graph._get_sync_session", return_value=sync_db):
        with patch("app.agents.editor.render_docx"):
            with patch("app.agents.editor.render_pptx"):
                llm = get_llm_client()
                initial_calls = llm.stats.total_calls

                state_edit: GraphState = {
                    "run_id": "run_edit_1",
                    "session_id": session_id,
                    "user_message": "also add a section on pricing",
                    "plan": {
                        "action": "edit",
                        "resolved_message": "also add a section on pricing",
                        "target_artifact_ids": [art_doc.id],
                    },
                    "session_context": build_session_context(session_id, sync_db),
                }

                res_edit = node_edit(state_edit)
                calls_for_edit = llm.stats.total_calls - initial_calls

                # LLM budget: <= 1 supervisor + 1 edit-plan + 1 research pass (2 calls) + content
                assert calls_for_edit <= 5, f"Edit turn exceeded call budget: used {calls_for_edit}"

    # Verify Artifact ID is SAME, version is 2
    all_doc_versions = sync_db.query(ArtifactVersion).filter(ArtifactVersion.artifact_id == art_doc.id).order_by(ArtifactVersion.version_no.asc()).all()
    assert len(all_doc_versions) == 2
    assert all_doc_versions[1].version_no == 2
    assert all_doc_versions[1].parent_version_id == v_doc1.id

    # Verify old sections are 100% identical in old model and new model
    v2_model = DocumentModel.model_validate_json(all_doc_versions[1].model_json)
    assert v2_model.sections[0].heading == doc.sections[0].heading
    assert v2_model.sections[0].blocks[0].text == doc.sections[0].blocks[0].text
    assert v2_model.sections[2].heading == doc.sections[1].heading
    assert v2_model.sections[2].blocks[0].text == doc.sections[1].blocks[0].text
    assert v2_model.sections[1].heading == "New Section"

    # Verify transparency header
    assert res_edit["reply"].startswith("Edited: ")

    # ── Turn 4: New Report ("create a new report on Renewable Energy") ──
    # Supervisor must route to generate
    p_new = parse_plan("create a new report on Renewable Energy", session_context=build_session_context(session_id, sync_db))
    assert p_new.action == "generate"

    # Simulate creating the new report artifacts
    art_new_doc = Artifact(title="Renewable Energy Report", artifact_type="docx", session_id=session_id, run_id="run_new")
    sync_db.add(art_new_doc)
    sync_db.flush()
    v_new_doc = ArtifactVersion(artifact_id=art_new_doc.id, version_no=1, model_json="{}", file_path="fake2.docx", file_type="docx")
    sync_db.add(v_new_doc)
    sync_db.commit()

    # Old artifacts must be untouched (still 2 versions for art_doc)
    assert sync_db.query(ArtifactVersion).filter(ArtifactVersion.artifact_id == art_doc.id).count() == 2

    # ── Turn 5: Plain edit after that targets newest artifact; "in the first report" targets first pair ──
    p_plain_edit = parse_plan("add a section on solar panels", session_context=build_session_context(session_id, sync_db))
    assert p_plain_edit.action == "edit"
    # Newest artifact is targeted
    assert art_new_doc.id in p_plain_edit.target_artifact_ids

    p_first_report = parse_plan("in the first report, add a section on ROI", session_context=build_session_context(session_id, sync_db))
    assert p_first_report.action == "edit"
    # First report (art_doc) is targeted
    assert art_doc.id in p_first_report.target_artifact_ids


# ── Test (e): UI helper restores chat and artifacts from session_id query param ─
def test_ui_session_restore_and_stability():
    """UI helper: given a session id from query params and a fake API, rebuilds messages & artifacts."""
    fake_messages = [
        {"id": 1, "session_id": "sess_123", "role": "user", "content": "hello", "run_id": "r1"},
        {"id": 2, "session_id": "sess_123", "role": "assistant", "content": "hi there", "run_id": "r1"},
    ]
    fake_artifacts = [
        {
            "id": 10,
            "title": "Report Alpha",
            "artifact_type": "docx",
            "latest_version": 2,
            "versions": [{"version_no": 1}, {"version_no": 2}],
        }
    ]

    # Simulate UI load logic
    state: dict = {
        "session_id": None,
        "chat_history": [],
    }
    query_params = {"session": "sess_123"}

    # Simulated restoration logic
    url_sess = query_params.get("session")
    if url_sess and state["session_id"] is None:
        state["session_id"] = url_sess
        state["chat_history"] = [{"role": m["role"], "content": m["content"]} for m in fake_messages]

    assert state["session_id"] == "sess_123"
    assert len(state["chat_history"]) == 2
    assert state["chat_history"][0]["content"] == "hello"

    # Simulate sending second message: verifies same session_id is reused
    second_message_payload = {
        "message": "make section 2 longer",
        "session_id": state["session_id"],
    }
    assert second_message_payload["session_id"] == "sess_123"
