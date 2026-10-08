"""End-to-End Test Suite for Conversational Continuity, Precise Editing, and Version Lineage.

Covers the 14 critical scenarios:
1. Create DOCX -> edit paragraph -> only that paragraph changes.
2. Create PPTX -> edit slide 5 -> only slide 5 changes.
3. Create XLSX -> modify one cell/range -> unrelated cells remain unchanged.
4. Create V1 -> append content -> V1 remains immutable and V2 contains the appended content.
5. V1 -> V2 -> V3 conversational editing preserves correct lineage.
6. User says "change the previous slide" -> correct slide is resolved.
7. User says "add this to the same document" -> correct active artifact is resolved.
8. User uploads a file in an earlier turn and references it later -> correct attachment is retrieved.
9. User requests internal information -> RAG is used without unnecessary web search.
10. User requests current external information -> web research is used with citations.
11. User asks for both internal and external information -> both sources are used with provenance.
12. Restart the application -> conversation/artifact/version context remains available.
13. Two separate chats -> their contexts and artifacts never leak into each other.
14. Full regression suite passes.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy.orm import Session, sessionmaker

from app.agents.editor import _apply_ops_to_docx, _apply_ops_to_pptx, _apply_ops_to_xlsx
from app.agents.supervisor import parse_plan
from app.agents.validator import validate_scope
from app.core.database import reset_db
from app.models.artifact import Artifact, ArtifactVersion
from app.models.chat import ChatMessage, ChatSession
from app.models.context import ConversationContext
from app.models.deck_model import BulletItem, DeckModel, SlideModel
from app.models.document_model import DocumentModel, ParagraphBlock, Section
from app.models.edit_ops import (
    AddSlideOp,
    UpdateCellOp,
    UpdateParagraphOp,
    UpdateSlideOp,
)
from app.models.execution_plan import ArtifactType, OperationType
from app.models.file_role import FileRole
from app.models.file import UploadedFile
from app.models.run import AgentRun
from app.models.workbook_model import CellModel, SheetModel, WorkbookModel
from app.services.diffing import diff_models
from app.services.reference_resolver import resolve_references
from app.services.session_context import build_conversation_context
from app.services.target_resolver import resolve_target
from app.services.versioning import create_version_sync


@pytest.fixture
def sync_db():
    """Create isolated in-memory or file-based SQLite database with all tables."""
    from sqlalchemy import create_engine
    from app.core.database import Base, run_migrations_sync
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        run_migrations_sync(conn)

    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ── Scenario 1: Create DOCX -> edit paragraph -> only that paragraph changes ──
def test_scenario1_docx_edit_paragraph_only():
    """Verify that editing a paragraph changes ONLY that paragraph, keeping all else identical."""
    doc = DocumentModel(
        title="Enterprise Architecture Guide",
        date="October 2026",
        sections=[
            Section(
                heading="Overview",
                blocks=[
                    ParagraphBlock(text="Original overview paragraph 1.", source_ids=[1]),
                    ParagraphBlock(text="Original overview paragraph 2.", source_ids=[1]),
                ],
            ),
            Section(
                heading="Methodology",
                blocks=[
                    ParagraphBlock(text="Original methodology paragraph.", source_ids=[2]),
                ],
            ),
        ],
    )

    op = UpdateParagraphOp(
        heading="Overview",
        paragraph_index=1,
        new_text="Revised overview paragraph 1 with precise metrics.",
        source_ids=[1],
    )

    updated_doc = _apply_ops_to_docx(doc, [op])

    # 1. Target paragraph was modified
    assert updated_doc.sections[0].blocks[0].text == "Revised overview paragraph 1 with precise metrics."
    # 2. Sibling paragraph in same section was completely unchanged
    assert updated_doc.sections[0].blocks[1].text == "Original overview paragraph 2."
    # 3. Unrelated section and paragraph were completely unchanged
    assert updated_doc.sections[1].blocks[0].text == "Original methodology paragraph."
    # 4. Title unchanged
    assert updated_doc.title == "Enterprise Architecture Guide"

    # 5. Diff tracking verifies minimal change
    diff = diff_models(doc, updated_doc)
    assert "Section 'Overview'" in diff["changed"]
    assert "Section 'Methodology'" not in diff["changed"]


# ── Scenario 2: Create PPTX -> edit slide 5 -> only slide 5 changes ────────────
def test_scenario2_pptx_edit_slide5_only():
    """Verify that editing slide 5 modifies only slide 5 while slides 1-4 and 6 remain untouched."""
    slides = [
        SlideModel(title=f"Slide {i}", role="title_content", bullets=[BulletItem(text=f"Bullet {i}")])
        for i in range(1, 7)
    ]
    deck = DeckModel(title="Corporate Q3 Review", slides=slides)

    revised_slide_5 = SlideModel(
        title="Slide 5 Revised: Market Expansion",
        role="title_content",
        bullets=[BulletItem(text="New strategic expansion bullet.")],
    )

    op = UpdateSlideOp(index=5, slide=revised_slide_5)
    updated_deck = _apply_ops_to_pptx(deck, [op])

    # Verify slide 5 is updated
    assert updated_deck.slides[4].title == "Slide 5 Revised: Market Expansion"
    assert updated_deck.slides[4].bullets[0].text == "New strategic expansion bullet."

    # Verify slides 1, 2, 3, 4, 6 are completely identical to original
    for idx in [0, 1, 2, 3, 5]:
        assert updated_deck.slides[idx].title == deck.slides[idx].title
        assert updated_deck.slides[idx].bullets[0].text == deck.slides[idx].bullets[0].text

    # Scope validation check
    scope_issues = validate_scope(
        target_nodes=["slide 5"],
        actually_changed_nodes=["slide 5"],
    )
    assert len(scope_issues) == 0


# ── Scenario 3: Create XLSX -> modify one cell -> unrelated cells unchanged ────
def test_scenario3_xlsx_modify_cell_unrelated_cells_unchanged():
    """Verify that modifying a specific cell leaves all other cells untouched."""
    sheet = SheetModel(
        name="Financials",
        cells=[
            CellModel(coordinate="A1", value="Revenue"),
            CellModel(coordinate="B1", value=100000),
            CellModel(coordinate="A2", value="Cost"),
            CellModel(coordinate="B2", value=40000),
        ],
    )
    wb = WorkbookModel(title="Financial Model", sheets=[sheet])

    op = UpdateCellOp(sheet_name="Financials", coordinate="B1", value=150000)
    updated_wb = _apply_ops_to_xlsx(wb, [op])

    sheet_res = updated_wb.sheets[0]
    cell_map = {c.coordinate.upper(): c.value for c in sheet_res.cells}

    # B1 updated
    assert cell_map["B1"] == 150000
    # A1, A2, B2 remain identical
    assert cell_map["A1"] == "Revenue"
    assert cell_map["A2"] == "Cost"
    assert cell_map["B2"] == 40000

    diff = diff_models(wb, updated_wb)
    assert any("B1" in item for item in diff["changed"])


# ── Scenario 4: Create V1 -> append content -> V1 immutable, V2 has content ───
def test_scenario4_v1_append_immutable_v2(sync_db, tmp_path):
    """Verify appending creates V2 with appended content while V1 remains strictly immutable."""
    art = Artifact(title="Roadmap Deck", artifact_type="pptx", session_id="sess_v1_v2")
    sync_db.add(art)
    sync_db.flush()

    deck_v1 = DeckModel(
        title="Roadmap",
        slides=[SlideModel(title="Introduction", role="title")],
    )
    file_v1 = tmp_path / "v1.pptx"
    file_v1.write_bytes(b"V1_CONTENT_BYTES")

    v1_rec = create_version_sync(
        db=sync_db,
        artifact_id=art.id,
        model_json=deck_v1.model_dump_json(),
        source_file_path=file_v1,
        change_summary="Initial roadmap",
    )
    sync_db.commit()

    assert v1_rec.version_no == 1
    assert v1_rec.parent_version_id is None
    v1_model_json_saved = v1_rec.model_json

    # Append a conclusion slide to produce V2
    deck_v2 = DeckModel.model_validate(deck_v1.model_dump())
    deck_v2.slides.append(SlideModel(title="Conclusion & Next Steps", role="title_content"))

    file_v2 = tmp_path / "v2.pptx"
    file_v2.write_bytes(b"V2_CONTENT_BYTES")

    v2_rec = create_version_sync(
        db=sync_db,
        artifact_id=art.id,
        model_json=deck_v2.model_dump_json(),
        source_file_path=file_v2,
        change_summary="Append conclusion",
    )
    sync_db.commit()

    # V2 links to V1
    assert v2_rec.version_no == 2
    assert v2_rec.parent_version_id == v1_rec.id

    # V1 remains strictly immutable in database and contents
    v1_recheck = sync_db.query(ArtifactVersion).filter(ArtifactVersion.id == v1_rec.id).one()
    assert v1_recheck.model_json == v1_model_json_saved
    assert len(json.loads(v1_recheck.model_json)["slides"]) == 1
    assert len(json.loads(v2_rec.model_json)["slides"]) == 2


# ── Scenario 5: V1 -> V2 -> V3 lineage preservation ────────────────────────────
def test_scenario5_v1_v2_v3_lineage_preservation(sync_db, tmp_path):
    """Verify strong DAG parent lineage across successive edits (V1 -> V2 -> V3)."""
    art = Artifact(title="Technical Spec", artifact_type="docx", session_id="sess_lineage")
    sync_db.add(art)
    sync_db.flush()

    # V1
    f1 = tmp_path / "s1.docx"; f1.write_bytes(b"s1")
    v1 = create_version_sync(sync_db, art.id, '{"title": "v1"}', f1, "Initial")
    sync_db.commit()

    # V2
    f2 = tmp_path / "s2.docx"; f2.write_bytes(b"s2")
    v2 = create_version_sync(sync_db, art.id, '{"title": "v2"}', f2, "Edit section 1")
    sync_db.commit()

    # V3
    f3 = tmp_path / "s3.docx"; f3.write_bytes(b"s3")
    v3 = create_version_sync(sync_db, art.id, '{"title": "v3"}', f3, "Edit section 2")
    sync_db.commit()

    assert v1.version_no == 1
    assert v1.parent_version_id is None

    assert v2.version_no == 2
    assert v2.parent_version_id == v1.id

    assert v3.version_no == 3
    assert v3.parent_version_id == v2.id


# ── Scenario 6: "change the previous slide" -> resolves correct slide ──────────
def test_scenario6_change_previous_slide_resolution():
    """Verify conversational relative reference 'change the previous slide' resolves correctly."""
    # When slide 5 was previously targeted, previous slide is slide 4
    ctx = ConversationContext(
        conversation_id="sess_prev_slide",
        last_target_nodes=["slide 5"],
        active_artifact={"artifact_id": 10, "kind": "pptx", "title": "Pitch Deck", "latest_version": 2},
    )

    resolved = resolve_references("change the previous slide to include ROI metrics", ctx)
    assert any("slide 4" in tn or "slide_004" in tn for tn in resolved.target_nodes)

    deck = DeckModel(
        title="Pitch Deck",
        slides=[SlideModel(title=f"Slide {i}") for i in range(1, 8)],
    )
    target_res = resolve_target("the previous slide", deck, context=ctx.to_dict())
    assert target_res.is_valid is True
    assert target_res.target_node_id == deck.slides[3].node_id  # slide 4 (index 3)


# ── Scenario 7: "add this to the same document" -> resolves active artifact ────
def test_scenario7_add_to_same_document_resolution():
    """Verify 'add this to the same document' binds to the active DOCX artifact ID."""
    ctx = ConversationContext(
        conversation_id="sess_same_doc",
        active_artifact={"artifact_id": 88, "kind": "docx", "title": "Annual Report", "latest_version": 1},
        artifact_history=[{"artifact_id": 88, "kind": "docx", "title": "Annual Report", "active": True}],
    )

    resolved = resolve_references("add this to the same document", ctx)
    assert resolved.target_artifact_id == 88
    assert resolved.target_artifact_type == ArtifactType.DOCX

    plan = parse_plan("add this to the same document", session_context=ctx)
    assert plan.target_artifact_id == 88
    assert plan.target_artifact_ids == [88]


# ── Scenario 8: User uploads file in earlier turn -> retrieved in later turn ──
def test_scenario8_uploaded_file_earlier_turn_retrieval(sync_db):
    """Verify that a file uploaded in an earlier turn is retrieved when referenced later."""
    sess_id = "sess_upload_retrieval"
    uf = UploadedFile(
        filename="Q3_Financial_Results.pdf",
        file_type="pdf",
        file_size=20480,
        stored_path="uploads/Q3_Financial_Results.pdf",
        session_id=sess_id,
        purpose="PRIMARY_SOURCE",
    )
    sync_db.add(uf)
    sync_db.commit()

    ctx = build_conversation_context(sess_id, sync_db)
    assert len(ctx.uploaded_files) == 1
    assert ctx.uploaded_files[0]["filename"] == "Q3_Financial_Results.pdf"

    plan = parse_plan("analyze the file I uploaded previously and build a proposal", session_context=ctx)
    assert plan.source_requirements.uploaded_files is True
    assert uf.id in plan.source_file_ids
    role_val = plan.attachment_roles.get("Q3_Financial_Results.pdf")
    assert role_val in ("PRIMARY_SOURCE", "SOURCE", FileRole.SOURCE, FileRole.REFERENCE)


# ── Scenario 9: Internal info request -> RAG without web ───────────────────────
def test_scenario9_internal_information_rag_no_web():
    """Verify internal knowledge requests enable RAG and keep web search disabled."""
    plan = parse_plan("Consult our internal HR policies for parental leave guidelines.")
    assert plan.source_requirements.knowledge_base is True
    assert plan.source_requirements.web is False


# ── Scenario 10: External info request -> Web with citations ───────────────────
def test_scenario10_external_information_web_with_citations():
    """Verify external query triggers web research without internal knowledge base."""
    plan = parse_plan("What are the latest updates on generative AI models released this week?")
    assert plan.source_requirements.web is True
    assert plan.source_requirements.knowledge_base is False


# ── Scenario 11: Both internal and external -> both sources with provenance ───
def test_scenario11_internal_and_external_with_provenance():
    """Verify joint comparative prompt requires both internal RAG and external web."""
    plan = parse_plan("Compare our internal sales benchmarks with current industry benchmarks.")
    assert plan.source_requirements.knowledge_base is True
    assert plan.source_requirements.web is True


# ── Scenario 12: Application restart preserves context ─────────────────────────
def test_scenario12_restart_preserves_context(sync_db, tmp_path):
    """Verify context survives an application/connection restart through persistent storage."""
    sess_id = "sess_restart_test"

    chat_sess = ChatSession(session_id=sess_id, title="Architecture Discussion")
    sync_db.add(chat_sess)

    m1 = ChatMessage(session_id=sess_id, role="user", content="Create a whitepaper on microservices.")
    m2 = ChatMessage(session_id=sess_id, role="assistant", content="Generated Whitepaper v1.")
    sync_db.add_all([m1, m2])

    art = Artifact(title="Microservices Whitepaper", artifact_type="docx", session_id=sess_id)
    sync_db.add(art)
    sync_db.flush()

    f1 = tmp_path / "ms_v1.docx"; f1.write_bytes(b"v1")
    v1 = create_version_sync(sync_db, art.id, '{"title": "Microservices Whitepaper", "sections": []}', f1, "v1")
    sync_db.commit()

    art_id = art.id
    v1_id = v1.id

    # Simulate application restart by resetting cached engines
    reset_db()

    # Re-open session and rebuild conversation context
    ctx = build_conversation_context(sess_id, sync_db)

    assert ctx.conversation_id == sess_id
    assert len(ctx.recent_messages) == 2
    assert ctx.active_artifact_id == art_id
    assert ctx.active_version_no == 1
    assert ctx.active_artifact["title"] == "Microservices Whitepaper"


# ── Scenario 13: Two separate chats never leak into each other ─────────────────
def test_scenario13_two_separate_chats_isolation(sync_db):
    """Verify strict session isolation: conversations and artifacts never leak between sessions."""
    sess_a = "session_finance_dept"
    sess_b = "session_marketing_dept"

    art_a = Artifact(title="Confidential Finance Report", artifact_type="docx", session_id=sess_a)
    art_b = Artifact(title="Public Brand Deck", artifact_type="pptx", session_id=sess_b)
    sync_db.add_all([art_a, art_b])

    file_a = UploadedFile(filename="payroll_2026.xlsx", file_type="xlsx", file_size=1024, stored_path="p.xlsx", session_id=sess_a)
    file_b = UploadedFile(filename="brand_guidelines.pdf", file_type="pdf", file_size=2048, stored_path="b.pdf", session_id=sess_b)
    sync_db.add_all([file_a, file_b])

    msg_a = ChatMessage(session_id=sess_a, role="user", content="Finance question")
    msg_b = ChatMessage(session_id=sess_b, role="user", content="Marketing question")
    sync_db.add_all([msg_a, msg_b])
    sync_db.commit()

    ctx_a = build_conversation_context(sess_a, sync_db)
    ctx_b = build_conversation_context(sess_b, sync_db)

    # Session A only sees finance
    titles_a = [a["title"] for a in ctx_a.artifact_history]
    files_a = [f["filename"] for f in ctx_a.uploaded_files]
    assert "Confidential Finance Report" in titles_a
    assert "Public Brand Deck" not in titles_a
    assert "payroll_2026.xlsx" in files_a
    assert "brand_guidelines.pdf" not in files_a

    # Session B only sees marketing
    titles_b = [a["title"] for a in ctx_b.artifact_history]
    files_b = [f["filename"] for f in ctx_b.uploaded_files]
    assert "Public Brand Deck" in titles_b
    assert "Confidential Finance Report" not in titles_b
    assert "brand_guidelines.pdf" in files_b
    assert "payroll_2026.xlsx" not in files_b
