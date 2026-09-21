"""LangGraph Supervisor Multi-Agent Pipeline for Document & Presentation Generation."""

from __future__ import annotations

import functools
import json
import logging
import pathlib
import time
import uuid
from pathlib import Path
from typing import Any, Callable

from langgraph.graph import END, START, StateGraph
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.agents.doc_analyzer import analyze_document
from app.agents.doc_generator import generate_document_model
from app.agents.ppt_analyzer import analyze_presentation
from app.agents.ppt_generator import generate_deck_model
from app.agents.state import GraphState
from app.agents.supervisor import Plan, parse_plan
from app.agents.validator import ValidationReport, validate_outputs
from app.core.config import get_settings
from app.llm.client import get_llm_client
from app.models.artifact import Artifact, ArtifactVersion
from app.models.deck_model import DeckModel
from app.models.document_model import DocumentModel
from app.models.file import UploadedFile
from app.models.trace import AgentTrace
from app.services.context_builder import build_context
from app.services.docx_renderer import render_docx
from app.services.pptx_renderer import render_pptx
from app.services.source_registry import SourceRegistry

logger = logging.getLogger(__name__)

DEFAULT_DOCX_TEMPLATE = Path("data/sample_templates/Company_Proposal.docx")
DEFAULT_PPTX_TEMPLATE = Path("data/sample_templates/Company_Template.pptx")


# ── Sync DB Session Helper for AgentTrace ──────────────────────────────────
_SyncSessionLocal: sessionmaker | None = None


def _get_sync_session() -> Session | None:
    """Get synchronous DB session for writing AgentTrace rows within graph nodes."""
    global _SyncSessionLocal
    if _SyncSessionLocal is None:
        try:
            settings = get_settings()
            # Convert aiosqlite URL to standard sqlite for sync execution
            sync_url = settings.database_url.replace("sqlite+aiosqlite:///", "sqlite:///")
            engine = create_engine(sync_url, connect_args={"check_same_thread": False})
            _SyncSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
        except Exception as exc:
            logger.warning(f"Failed to create sync DB engine for tracing: {exc}")
            return None

    try:
        return _SyncSessionLocal()
    except Exception as exc:
        logger.warning(f"Failed to open sync DB session: {exc}")
        return None


# ── Traced Decorator ───────────────────────────────────────────────────────
def traced(agent_name: str) -> Callable:
    """Decorator for LangGraph nodes recording AgentTrace rows and handling errors gracefully."""

    def decorator(func: Callable[[GraphState], GraphState]) -> Callable[[GraphState], GraphState]:
        @functools.wraps(func)
        def wrapper(state: GraphState) -> GraphState:
            start_time = time.perf_counter()
            run_id = state.get("run_id", str(uuid.uuid4()))
            input_summary = f"User msg: '{state.get('user_message', '')[:100]}'"
            status = "ok"
            output_summary = ""
            err_msg = None

            try:
                res_state = func(state)
                output_summary = f"Completed node {agent_name}"
                return res_state
            except Exception as exc:
                status = "error"
                err_msg = f"Error in node '{agent_name}': {str(exc)}"
                logger.error(err_msg, exc_info=True)

                # Graceful degradation: append to state errors rather than crashing graph
                errors = list(state.get("errors", []))
                errors.append(err_msg)
                state["errors"] = errors
                output_summary = f"Failed with error: {str(exc)[:150]}"
                return state
            finally:
                duration_ms = int((time.perf_counter() - start_time) * 1000)
                db_sess = _get_sync_session()
                if db_sess:
                    try:
                        trace_row = AgentTrace(
                            trace_id=run_id,
                            agent_name=agent_name,
                            input_summary=input_summary[:500],
                            output_summary=output_summary[:500],
                            duration_ms=duration_ms,
                            status=status,
                        )
                        db_sess.add(trace_row)
                        db_sess.commit()
                        db_sess.close()
                    except Exception as t_exc:
                        logger.warning(f"Failed to write AgentTrace row: {t_exc}")

        return wrapper

    return decorator


# ── Node Implementations ───────────────────────────────────────────────────

@traced("plan_node")
def node_plan(state: GraphState) -> GraphState:
    """ONE LLM call to parse user message into a structured Plan."""
    user_msg = state.get("user_message", "")
    file_ids = state.get("file_ids", [])
    
    plan_obj = parse_plan(user_msg, file_ids=file_ids)
    
    # Resolve file templates if uploaded file_ids present
    if file_ids:
        db_sess = _get_sync_session()
        if db_sess:
            try:
                files = db_sess.query(UploadedFile).filter(UploadedFile.id.in_(file_ids)).all()
                for f in files:
                    if f.filename.endswith(".docx") and not plan_obj.doc_template_file_id:
                        plan_obj.doc_template_file_id = f.id
                    elif f.filename.endswith(".pptx") and not plan_obj.ppt_template_file_id:
                        plan_obj.ppt_template_file_id = f.id
                db_sess.close()
            except Exception as exc:
                logger.warning(f"Failed to resolve uploaded template file IDs: {exc}")

    state["plan"] = plan_obj.model_dump()
    state["retry_count"] = 0
    state["errors"] = list(state.get("errors", []))
    state["artifacts"] = list(state.get("artifacts", []))
    return state


@traced("stub_node")
def node_stub(state: GraphState) -> GraphState:
    """Stub node for unsupported actions ('edit' or 'convert')."""
    plan_data = state.get("plan", {})
    action = plan_data.get("action", "edit")
    state["reply"] = f"The '{action}' feature is coming in the next step! Stay tuned."
    state["artifacts"] = []
    return state


@traced("answer_node")
def node_answer(state: GraphState) -> GraphState:
    """Answer node for plain Q&A without creating files."""
    user_msg = state.get("user_message", "")
    ctx = build_context(user_msg, use_web=True, use_kb=True)
    
    llm = get_llm_client()
    sources_formatted = "\n".join(
        f"[{sid}] {s.get('title')}: {s.get('snippet')[:150]}"
        for sid, s in ctx.sources_map.items()
    )
    prompt = (
        f"User Question: '{user_msg}'\n\n"
        f"Retrieved Context & Sources:\n{sources_formatted}\n\n"
        "Provide a clear, authoritative, structured answer. Cite sources using [id] brackets."
    )
    try:
        reply_text = llm.generate_text(prompt, system="You are an enterprise AI assistant.")
    except Exception as exc:
        reply_text = f"An error occurred while answering: {exc}"

    state["reply"] = reply_text
    state["registry"] = ctx.sources_map
    state["artifacts"] = []
    return state


@traced("analyze_templates_node")
def node_analyze_templates(state: GraphState) -> GraphState:
    """Analyze DOCX and PPTX templates to extract layout, style, and tone profiles."""
    plan_data = state.get("plan", {})
    doc_file_id = plan_data.get("doc_template_file_id")
    ppt_file_id = plan_data.get("ppt_template_file_id")

    doc_tmpl_path = DEFAULT_DOCX_TEMPLATE
    ppt_tmpl_path = DEFAULT_PPTX_TEMPLATE

    db_sess = _get_sync_session()
    if db_sess:
        try:
            if doc_file_id:
                rec = db_sess.query(UploadedFile).filter(UploadedFile.id == doc_file_id).first()
                if rec and Path(rec.stored_path).exists():
                    doc_tmpl_path = Path(rec.stored_path)
            if ppt_file_id:
                rec = db_sess.query(UploadedFile).filter(UploadedFile.id == ppt_file_id).first()
                if rec and Path(rec.stored_path).exists():
                    ppt_tmpl_path = Path(rec.stored_path)
            db_sess.close()
        except Exception as exc:
            logger.warning(f"Error querying UploadedFile records: {exc}")

    doc_profile = analyze_document(doc_tmpl_path)
    ppt_profile = analyze_presentation(ppt_tmpl_path)

    state["template_profiles"] = {
        "doc_profile": doc_profile.model_dump(),
        "ppt_profile": ppt_profile.model_dump(),
        "doc_template_path": str(doc_tmpl_path),
        "ppt_template_path": str(ppt_tmpl_path),
    }
    return state


@traced("gather_context_node")
def node_gather_context(state: GraphState) -> GraphState:
    """Run RAG retrieval and web research sequentially to populate SourceRegistry."""
    plan_data = state.get("plan", {})
    topic = plan_data.get("topic") or state.get("user_message", "")
    use_web = plan_data.get("use_web", True)
    use_kb = plan_data.get("use_kb", True)

    ctx = build_context(topic, use_web=use_web, use_kb=use_kb)

    state["registry"] = ctx.sources_map
    state["findings"] = [f.model_dump() for f in ctx.findings]
    return state


@traced("generate_doc_node")
def node_generate_doc(state: GraphState) -> GraphState:
    """Generate structured DocumentModel if requested in plan."""
    plan_data = state.get("plan", {})
    outputs = plan_data.get("outputs", ["docx"])

    if "docx" not in outputs:
        return state

    topic = plan_data.get("topic") or state.get("user_message", "")
    profiles = state.get("template_profiles", {})
    doc_prof_dict = profiles.get("doc_profile")

    from app.models.template_profile import TemplateProfile
    doc_profile = TemplateProfile.model_validate(doc_prof_dict) if doc_prof_dict else analyze_document(DEFAULT_DOCX_TEMPLATE)

    sources_map = state.get("registry", {})
    sources_list = list(sources_map.values()) if sources_map else []

    # Feedback from previous failed validation retry
    retry_feedback = ""
    validation_data = state.get("validation")
    if validation_data and not validation_data.get("passed"):
        issues = validation_data.get("issues", [])
        issue_msgs = "; ".join(i.get("message", "") for i in issues if "docx" in i.get("where", ""))
        if issue_msgs:
            retry_feedback = f"\n\nCRITICAL FIX REQUIRED FROM PREVIOUS RETRY:\nFix the following validation issues: {issue_msgs}"

    doc_model = generate_document_model(
        brief=topic + retry_feedback,
        profile=doc_profile,
        sources=sources_list,
    )
    state["doc_model"] = doc_model.model_dump()
    return state


@traced("generate_deck_node")
def node_generate_deck(state: GraphState) -> GraphState:
    """Generate structured DeckModel if requested in plan."""
    plan_data = state.get("plan", {})
    outputs = plan_data.get("outputs", ["pptx"])

    if "pptx" not in outputs:
        return state

    topic = plan_data.get("topic") or state.get("user_message", "")
    slide_count = plan_data.get("slide_count", 12)
    profiles = state.get("template_profiles", {})
    ppt_prof_dict = profiles.get("ppt_profile")

    from app.models.template_profile import TemplateProfile
    ppt_profile = TemplateProfile.model_validate(ppt_prof_dict) if ppt_prof_dict else analyze_presentation(DEFAULT_PPTX_TEMPLATE)

    sources_map = state.get("registry", {})
    sources_list = list(sources_map.values()) if sources_map else []

    # Feedback from previous failed validation retry
    retry_feedback = ""
    validation_data = state.get("validation")
    if validation_data and not validation_data.get("passed"):
        issues = validation_data.get("issues", [])
        issue_msgs = "; ".join(i.get("message", "") for i in issues if "pptx" in i.get("where", ""))
        if issue_msgs:
            retry_feedback = f"\n\nCRITICAL FIX REQUIRED FROM PREVIOUS RETRY:\nFix the following validation issues: {issue_msgs}"

    deck_model = generate_deck_model(
        brief=topic + retry_feedback,
        profile=ppt_profile,
        sources=sources_list,
        slide_count=slide_count,
    )
    state["deck_model"] = deck_model.model_dump()
    return state


@traced("validate_node")
def node_validate(state: GraphState) -> GraphState:
    """Perform deterministic quality checks on generated models."""
    plan_data = state.get("plan", {})
    expected_slides = plan_data.get("slide_count", 12)
    sources_map = state.get("registry", {})
    valid_sids = set(sources_map.keys())

    doc_model_dict = state.get("doc_model")
    deck_model_dict = state.get("deck_model")

    doc_model = DocumentModel.model_validate(doc_model_dict) if doc_model_dict else None
    deck_model = DeckModel.model_validate(deck_model_dict) if deck_model_dict else None

    report = validate_outputs(
        doc_model=doc_model,
        deck_model=deck_model,
        expected_slide_count=expected_slides,
        valid_source_ids=valid_sids,
    )

    state["validation"] = report.model_dump()
    return state


@traced("finalize_node")
def node_finalize(state: GraphState) -> GraphState:
    """Render DOCX and PPTX files, save DB records, and construct final user reply."""
    profiles = state.get("template_profiles", {})
    doc_tmpl_path = Path(profiles.get("doc_template_path", str(DEFAULT_DOCX_TEMPLATE)))
    ppt_tmpl_path = Path(profiles.get("ppt_template_path", str(DEFAULT_PPTX_TEMPLATE)))

    from app.models.template_profile import TemplateProfile
    doc_prof_dict = profiles.get("doc_profile")
    ppt_prof_dict = profiles.get("ppt_profile")

    doc_profile = TemplateProfile.model_validate(doc_prof_dict) if doc_prof_dict else analyze_document(doc_tmpl_path)
    ppt_profile = TemplateProfile.model_validate(ppt_prof_dict) if ppt_prof_dict else analyze_presentation(ppt_tmpl_path)

    sources_map = state.get("registry", {})
    doc_model_dict = state.get("doc_model")
    deck_model_dict = state.get("deck_model")

    output_dir = Path("data/outputs")
    output_dir.mkdir(parents=True, exist_ok=True)
    artifacts_created: list[dict[str, Any]] = []

    db_sess = _get_sync_session()

    # 1. Render & Save DOCX
    if doc_model_dict:
        doc_model = DocumentModel.model_validate(doc_model_dict)
        out_docx = output_dir / f"Proposal_{uuid.uuid4().hex[:8]}.docx"
        render_docx(doc_model, doc_tmpl_path, doc_profile, out_docx, sources_map=sources_map)
        
        if db_sess:
            try:
                art = Artifact(artifact_type="docx", title=doc_model.title)
                db_sess.add(art)
                db_sess.flush()

                ver = ArtifactVersion(
                    artifact_id=art.id,
                    version_no=1,
                    model_json=doc_model.model_dump_json(),
                    file_path=str(out_docx),
                    file_type="docx",
                    change_summary="Initial generation",
                )
                db_sess.add(ver)
                db_sess.commit()

                artifacts_created.append({
                    "kind": "docx",
                    "artifact_id": art.id,
                    "version": 1,
                    "title": art.title,
                    "download_url": f"/artifacts/{art.id}/download?version=1",
                    "file_path": str(out_docx),
                })
            except Exception as exc:
                logger.warning(f"Failed to record DOCX artifact DB entry: {exc}")

    # 2. Render & Save PPTX
    if deck_model_dict:
        deck_model = DeckModel.model_validate(deck_model_dict)
        out_pptx = output_dir / f"Deck_{uuid.uuid4().hex[:8]}.pptx"
        render_pptx(deck_model, ppt_tmpl_path, ppt_profile, out_pptx, sources_map=sources_map)

        if db_sess:
            try:
                art = Artifact(artifact_type="pptx", title=deck_model.title)
                db_sess.add(art)
                db_sess.flush()

                ver = ArtifactVersion(
                    artifact_id=art.id,
                    version_no=1,
                    model_json=deck_model.model_dump_json(),
                    file_path=str(out_pptx),
                    file_type="docx",
                    change_summary="Initial generation",
                )
                db_sess.add(ver)
                db_sess.commit()

                artifacts_created.append({
                    "kind": "pptx",
                    "artifact_id": art.id,
                    "version": 1,
                    "title": art.title,
                    "download_url": f"/artifacts/{art.id}/download?version=1",
                    "file_path": str(out_pptx),
                })
            except Exception as exc:
                logger.warning(f"Failed to record PPTX artifact DB entry: {exc}")

    if db_sess:
        db_sess.close()

    # 3. Format user reply with download links & citations summary
    reply_parts = ["### Generation Completed Successfully\n"]
    if artifacts_created:
        reply_parts.append("**Generated Artifacts:**")
        for a in artifacts_created:
            reply_parts.append(f"- [{a['kind'].upper()}] **{a['title']}** ([Download]({a['download_url']}))")
        reply_parts.append("")

    if sources_map:
        reply_parts.append(f"**Citations ({len(sources_map)} Sources Registered):**")
        for sid, s in list(sources_map.items())[:5]:
            reply_parts.append(f"- `[{sid}]` {s.get('title')} ({s.get('url')})")
        if len(sources_map) > 5:
            reply_parts.append(f"  *...and {len(sources_map) - 5} additional sources.*")

    # Add warnings if any degradation occurred
    errors = state.get("errors", [])
    if errors:
        reply_parts.append("\n> **Note / Warnings:**")
        for err in errors:
            reply_parts.append(f"> - {err}")

    state["reply"] = "\n".join(reply_parts)
    state["artifacts"] = artifacts_created
    return state


# ── Conditional Routing Edge ───────────────────────────────────────────────

def route_intent(state: GraphState) -> str:
    """Route plan action to appropriate entry node."""
    plan_data = state.get("plan", {})
    action = plan_data.get("action", "generate")

    if action == "answer":
        return "answer_node"
    elif action in ("edit", "convert"):
        return "stub_node"
    else:
        return "analyze_templates_node"


def should_retry(state: GraphState) -> str:
    """Check validation report and decide whether to retry generation once."""
    val_data = state.get("validation", {})
    passed = val_data.get("passed", True)
    retry_count = state.get("retry_count", 0)

    if not passed and retry_count < 1:
        state["retry_count"] = retry_count + 1
        logger.info("Validation failed. Retrying generation once with issue feedback...")
        plan_data = state.get("plan", {})
        outputs = plan_data.get("outputs", ["docx", "pptx"])
        if "docx" in outputs:
            return "generate_doc_node"
        else:
            return "generate_deck_node"
    return "finalize_node"


# ── StateGraph Assembly ───────────────────────────────────────────────────

def build_graph() -> StateGraph:
    """Assemble and compile the LangGraph supervisor workflow."""
    builder = StateGraph(GraphState)

    # Add Nodes
    builder.add_node("plan_node", node_plan)
    builder.add_node("stub_node", node_stub)
    builder.add_node("answer_node", node_answer)
    builder.add_node("analyze_templates_node", node_analyze_templates)
    builder.add_node("gather_context_node", node_gather_context)
    builder.add_node("generate_doc_node", node_generate_doc)
    builder.add_node("generate_deck_node", node_generate_deck)
    builder.add_node("validate_node", node_validate)
    builder.add_node("finalize_node", node_finalize)

    # Add Edges
    builder.add_edge(START, "plan_node")
    
    # Conditional edge from plan
    builder.add_conditional_edges(
        "plan_node",
        route_intent,
        {
            "answer_node": "answer_node",
            "stub_node": "stub_node",
            "analyze_templates_node": "analyze_templates_node",
        },
    )

    builder.add_edge("stub_node", END)
    builder.add_edge("answer_node", END)

    # Generation flow edges
    builder.add_edge("analyze_templates_node", "gather_context_node")
    builder.add_edge("gather_context_node", "generate_doc_node")
    builder.add_edge("generate_doc_node", "generate_deck_node")
    builder.add_edge("generate_deck_node", "validate_node")

    # Conditional retry edge
    builder.add_conditional_edges(
        "validate_node",
        should_retry,
        {
            "generate_doc_node": "generate_doc_node",
            "generate_deck_node": "generate_deck_node",
            "finalize_node": "finalize_node",
        },
    )

    builder.add_edge("finalize_node", END)

    return builder.compile()


# Compiled app graph instance
graph_app = build_graph()
