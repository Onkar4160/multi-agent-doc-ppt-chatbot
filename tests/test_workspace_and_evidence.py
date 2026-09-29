"""Unit and integration tests for Part A (Workspace) & Part B (Evidence Pack Grounding)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.agents.converter import (
    CONVERT_DOC_TO_PPT_PROMPT,
    CONVERT_PPT_TO_DOC_PROMPT,
    convert_artifact,
)
from app.agents.doc_generator import DOC_GEN_PROMPT
from app.agents.editor import EDITOR_PROMPT, edit_artifact
from app.agents.graph import (
    node_analyze_templates,
    node_answer,
)
from app.agents.ppt_generator import PPT_GEN_PROMPT
from app.agents.state import GraphState
from app.core.config import get_settings
from app.core.database import Base
from app.llm.client import get_llm_client
from app.models.artifact import Artifact, ArtifactVersion

DEFAULT_DOCX_TEMPLATE = Path(get_settings().default_docx_template_path)
DEFAULT_PPTX_TEMPLATE = Path(get_settings().default_pptx_template_path)
from app.models.file import UploadedFile
from app.models.workspace import Workspace
from app.services.context_builder import ContextResult, build_context
from app.services.evidence_pack import (
    EvidenceItem,
    build_evidence_pack,
    format_evidence_pack,
)

GROUNDING_RULE_VERBATIM = (
    "You may only state a specific fact (number, date, statistic, name, claimed event) if it "
    "matches an entry in the EVIDENCE PACK below, citing that entry's exact id as source_id. "
    "General connective text needs no citation but must contain no invented specific fact. "
    "If something isn't covered by the evidence pack, omit it or state it as general knowledge "
    "without invented precision — never fabricate a number or date."
)

NO_NEW_FACTS_RULE_VERBATIM = (
    "Do not introduce any new factual claim while converting. Only reorganize or rephrase "
    "EXISTING content, and preserve every source_id exactly as given. Do not invent new citations."
)


@pytest.fixture
def sync_db_session():
    """Create a temporary sqlite session with all tables."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    from app.services.workspace_init import init_default_workspace_sync
    init_default_workspace_sync(session)
    yield session
    session.close()


# 1. Evidence Pack Splitting without LLM
def test_build_evidence_pack_splits_kb_chunk_without_llm():
    """Test build_evidence_pack splits multi-sentence KB chunks without making any LLM calls."""
    llm = get_llm_client()
    initial_calls = llm.stats.total_calls

    kb_chunk = {
        "source_id": 10,
        "text": (
            "Enterprise AI adoption increased by 45% in 2024. Multi-agent workflows "
            "reduced processing time by 60%. The solution operates securely within customer cloud environments."
        ),
    }

    pack = build_evidence_pack(findings=[], kb_hits=[kb_chunk])

    # Assert ZERO LLM calls were made
    delta_calls = llm.stats.total_calls - initial_calls
    assert delta_calls == 0, f"Expected 0 LLM calls, but got {delta_calls}"

    # Assert multiple atomic pieces were created and properly capped
    assert len(pack) >= 2
    for item in pack:
        assert isinstance(item, EvidenceItem)
        assert item.source_id == 10
        assert len(item.text) <= 200
        assert item.id > 0

    # Test format_evidence_pack
    formatted = format_evidence_pack(pack)
    assert "[1]" in formatted
    assert "[2]" in formatted


# 2. Doc/Deck Generation Prompts Contain Evidence Pack and Grounding Rule
def test_generation_prompts_contain_evidence_pack_and_grounding_rule():
    """Verify DOC_GEN_PROMPT and PPT_GEN_PROMPT contain evidence pack block and exact grounding rule."""
    # Check DOCX Prompt
    assert "--- EVIDENCE PACK ---" in DOC_GEN_PROMPT
    assert GROUNDING_RULE_VERBATIM in DOC_GEN_PROMPT
    assert "NexaWorks" not in DOC_GEN_PROMPT

    # Check PPTX Prompt
    assert "--- EVIDENCE PACK ---" in PPT_GEN_PROMPT
    assert GROUNDING_RULE_VERBATIM in PPT_GEN_PROMPT
    assert "NexaWorks" not in PPT_GEN_PROMPT

    # Check Editor Prompt
    assert "--- EVIDENCE PACK ---" in EDITOR_PROMPT
    assert GROUNDING_RULE_VERBATIM in EDITOR_PROMPT


# 3. Converter Prompt Contains the "no new facts" Rule
def test_converter_prompt_contains_no_new_facts_rule():
    """Verify converter prompts contain the exact 'no new facts' rule."""
    assert NO_NEW_FACTS_RULE_VERBATIM in CONVERT_DOC_TO_PPT_PROMPT
    assert NO_NEW_FACTS_RULE_VERBATIM in CONVERT_PPT_TO_DOC_PROMPT


# 4. Workspace Creation Persists Profile and Repeat Generation Saves Exactly 2 LLM Calls
def test_workspace_creation_and_repeat_generation_llm_delta():
    """Test workspace creation persists profiles, and repeat generation makes 2 fewer LLM calls."""
    llm = get_llm_client()
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)

    # 1. Create uploaded files
    with session_factory() as sess:
        docx_file = UploadedFile(
            filename="Company_Proposal.docx",
            file_type="docx",
            file_size=1024,
            stored_path=str(DEFAULT_DOCX_TEMPLATE.resolve()),
        )
        pptx_file = UploadedFile(
            filename="Green Cream Simple Aesthetic Watercolor Presentation.pptx",
            file_type="pptx",
            file_size=2048,
            stored_path=str(DEFAULT_PPTX_TEMPLATE.resolve()),
        )
        sess.add_all([docx_file, pptx_file])
        sess.commit()
        docx_id = docx_file.id
        pptx_id = pptx_file.id

        # Create active workspace without cached profiles
        ws = Workspace(
            name="Acme Corp",
            docx_template_file_id=docx_id,
            pptx_template_file_id=pptx_id,
            doc_profile_json=None,
            ppt_profile_json=None,
            is_active=True,
        )
        sess.add(ws)
        sess.commit()
        ws_id = ws.id

    # 2. First generation request (profiles not yet cached: analyzes templates, 2 tone LLM calls)
    with patch("app.agents.graph._get_sync_session", side_effect=session_factory):
        state_first: GraphState = {
            "user_message": "Create a proposal",
            "file_ids": [],
            "plan": {"action": "generate"},
        }

        calls_before_1 = llm.stats.total_calls
        res_first = node_analyze_templates(state_first)
        calls_first = llm.stats.total_calls - calls_before_1

        assert calls_first == 2, f"Expected first call to make 2 LLM calls for tone analysis, got {calls_first}"
        assert "doc_profile" in res_first["template_profiles"]
        assert "ppt_profile" in res_first["template_profiles"]

        # 3. Verify profiles were persisted in Workspace row
        with session_factory() as sess:
            saved_ws = sess.query(Workspace).filter(Workspace.id == ws_id).first()
            assert saved_ws.is_active is True
            assert saved_ws.doc_profile_json is not None
            assert saved_ws.ppt_profile_json is not None

        # 4. Second generation request WITH active workspace (loads profiles directly, 0 LLM calls)
        state_second: GraphState = {
            "user_message": "Create another proposal",
            "file_ids": [],
            "plan": {"action": "generate"},
        }

        calls_before_2 = llm.stats.total_calls
        res_second = node_analyze_templates(state_second)
        calls_second = llm.stats.total_calls - calls_before_2

        assert calls_second == 0, f"Expected second call to make 0 LLM calls, got {calls_second}"

        # LLM delta: second call makes exactly 2 fewer LLM calls!
        llm_delta = calls_first - calls_second
        assert llm_delta == 2, f"Expected LLM delta of 2 fewer calls, got {llm_delta}"



# 5. refresh_with_web Uses Freshly Built Evidence Pack
def test_refresh_with_web_uses_freshly_built_evidence_pack(sync_db_session):
    """Test that refresh_with_web builds a fresh evidence pack and grounds using it."""
    from app.models.document_model import DocumentModel, Section, ParagraphBlock

    doc_m = DocumentModel(
        title="Test Proposal",
        sections=[
            Section(
                heading="Overview",
                blocks=[ParagraphBlock(text="Initial section content.", source_ids=[1])],
            )
        ],
    )

    art = Artifact(title="Doc", artifact_type="docx")
    sync_db_session.add(art)
    sync_db_session.flush()

    ver = ArtifactVersion(
        artifact_id=art.id,
        version_no=1,
        model_json=doc_m.model_dump_json(),
        file_path="dummy.docx",
        file_type="docx",
        source_ids_json=json.dumps([1]),
    )
    sync_db_session.add(ver)
    sync_db_session.commit()

    captured_prompts = []
    real_generate_json = get_llm_client().generate_json

    def mock_gen_json(prompt, schema, **kwargs):
        captured_prompts.append(prompt)
        from app.models.edit_ops import EditPlan, RefreshWithWebOp
        if schema == EditPlan:
            return EditPlan(
                target="docx",
                summary="Refresh with latest web",
                ops=[RefreshWithWebOp(topic="AI Market 2026")],
            )
        return real_generate_json(prompt, schema, **kwargs)

    with patch.object(get_llm_client(), "generate_json", side_effect=mock_gen_json):
        res = edit_artifact(
            artifact_id=art.id,
            instruction="Refresh with web information",
            db_session=sync_db_session,
        )

        assert res.new_version_no == 2
        # Check that the prompt sent to the LLM during refresh contains FRESH EVIDENCE PACK and grounding rule
        refresh_prompts = [p for p in captured_prompts if "FRESH EVIDENCE PACK" in p]
        assert len(refresh_prompts) >= 1
        assert "--- EVIDENCE PACK ---" in refresh_prompts[0]
        assert GROUNDING_RULE_VERBATIM in refresh_prompts[0]


# 6. Answer Node Uses Evidence Pack Grounding
def test_answer_node_evidence_pack_grounding():
    """Test that answer_node builds an evidence pack, grounds on it, and produces 0 artifacts."""
    state: GraphState = {
        "user_message": "What is the timeline of the project?",
        "plan": {"action": "answer", "use_web": False, "use_kb": True},
    }

    res_state = node_answer(state)

    assert "reply" in res_state
    assert len(res_state["reply"]) > 0
    assert res_state["artifacts"] == []
    assert "evidence_pack" in res_state


def test_no_literal_strings_in_agent_prompts():
    """Verify no literal company name, location, or currency exist in agent prompts."""
    from app.agents.doc_generator import DOC_GEN_PROMPT
    from app.agents.ppt_generator import PPT_GEN_PROMPT
    from app.agents.editor import EDITOR_PROMPT
    from app.agents.converter import CONVERT_DOC_TO_PPT_PROMPT, CONVERT_PPT_TO_DOC_PROMPT
    from app.agents.supervisor import SUPERVISOR_PROMPT

    all_prompts = [
        DOC_GEN_PROMPT,
        PPT_GEN_PROMPT,
        EDITOR_PROMPT,
        CONVERT_DOC_TO_PPT_PROMPT,
        CONVERT_PPT_TO_DOC_PROMPT,
        SUPERVISOR_PROMPT,
    ]

    banned_literals = ["NexaWorks", "Pune", "India", "INR", "Apex Global"]
    for prompt in all_prompts:
        for literal in banned_literals:
            assert literal.lower() not in prompt.lower(), f"Found banned literal '{literal}' in prompt:\n{prompt}"


def test_dynamic_outline_unusual_topic():
    """Verify generating on an unusual topic produces a coherent, non-empty DocumentModel."""
    from app.agents.doc_generator import generate_document_model
    from app.models.template_profile import TemplateProfile

    profile = TemplateProfile(source_name="Custom_Doc.docx")
    unusual_topic = "Bioluminescent deep sea organisms and their metabolic adaptations"
    doc = generate_document_model(
        brief=unusual_topic,
        profile=profile,
        document_type="scientific_discovery",
    )
    assert doc.title != ""
    assert len(doc.sections) >= 1
    assert any(len(s.blocks) > 0 for s in doc.sections)


def test_deck_model_slide_count_exact_match():
    """Verify DeckModel slide count strictly matches the requested count."""
    from app.agents.ppt_generator import generate_deck_model
    from app.models.template_profile import TemplateProfile

    profile = TemplateProfile(source_name="Custom_Deck.pptx")
    for req_count in [5, 8, 12]:
        deck = generate_deck_model(
            brief="Overview of renewable hydrogen infrastructure",
            profile=profile,
            slide_count=req_count,
            document_type="strategy_brief",
        )
        assert len(deck.slides) == req_count, f"Expected {req_count} slides, got {len(deck.slides)}"

