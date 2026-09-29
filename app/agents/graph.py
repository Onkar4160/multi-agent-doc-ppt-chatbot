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

from app.agents.converter import convert_artifact
from app.agents.editor import edit_artifact
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
from app.models.workspace import Workspace
from app.services.context_builder import build_context
from app.services.docx_renderer import render_docx
from app.services.evidence_pack import EvidenceItem, build_evidence_pack, format_evidence_pack
from app.services.pptx_renderer import render_pptx
from app.services.session_context import build_session_context
from app.services.source_registry import SourceRegistry

logger = logging.getLogger(__name__)



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
            _SyncSessionLocal = sessionmaker(
                bind=engine,
                autoflush=False,
                autocommit=False,
                expire_on_commit=False,
            )
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
    """ONE LLM call to parse user message into a structured Plan with session context."""
    user_msg = state.get("user_message", "")
    file_ids = state.get("file_ids", [])
    session_id = state.get("session_id", "")

    db_sess = _get_sync_session()
    session_ctx = None
    if db_sess and session_id:
        try:
            session_ctx = build_session_context(session_id, db_sess)
        except Exception as exc:
            logger.warning(f"Failed to build session context: {exc}")

    plan_obj = parse_plan(user_msg, file_ids=file_ids, session_context=session_ctx)

    # Always resolve templates from the single active Workspace
    if db_sess:
        try:
            active_ws = (
                db_sess.query(Workspace).filter(Workspace.is_active == True).first()
            )
            if active_ws:
                plan_obj.doc_template_file_id = active_ws.docx_template_file_id
                plan_obj.ppt_template_file_id = active_ws.pptx_template_file_id
            db_sess.close()
        except Exception as exc:
            logger.warning(f"Failed to resolve active workspace template file IDs: {exc}")

    state["plan"] = plan_obj.model_dump()
    state["session_context"] = session_ctx
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
    """Answer node for grounded Q&A. Supports 'document' scope (1 LLM call, no search) or 'fresh' scope."""
    import re
    user_msg = state.get("user_message", "")
    session_id = state.get("session_id", "")
    plan_data = state.get("plan", {})
    ans_scope = plan_data.get("answer_scope", "fresh")
    resolved_msg = plan_data.get("resolved_message") or user_msg

    db_sess = _get_sync_session()
    llm = get_llm_client()

    if ans_scope == "document":
        target_ids = plan_data.get("target_artifact_ids", [])
        target_art = None
        if db_sess and session_id:
            try:
                query = db_sess.query(Artifact).filter(Artifact.session_id == session_id)
                if target_ids:
                    target_art = query.filter(Artifact.id.in_(target_ids)).first()
                if not target_art:
                    target_art = query.order_by(Artifact.id.desc()).first()
            except Exception as exc:
                logger.warning(f"Failed to query target artifact for answer: {exc}")

        if not target_art or not db_sess:
            state["reply"] = (
                "The current session does not have any generated documents or presentations to answer from. "
                "I can research and create a report on this topic if you'd like!"
            )
            state["artifacts"] = []
            if db_sess:
                db_sess.close()
            return state

        target_art_id = target_art.id
        target_title = str(target_art.title)
        target_type = str(target_art.artifact_type)

        # Get latest version model_json
        ver_rec = (
            db_sess.query(ArtifactVersion)
            .filter(ArtifactVersion.artifact_id == target_art_id)
            .order_by(ArtifactVersion.version_no.desc())
            .first()
        )
        model_json_str = ver_rec.model_json if ver_rec else None
        db_sess.close()

        if not model_json_str:
            state["reply"] = "The existing document has no readable content to answer from."
            state["artifacts"] = []
            return state

        # Extract text and source IDs by keyword / heading match without LLM
        extracted_content = []
        try:
            m_data = json.loads(model_json_str)
            query_words = set(re.findall(r"\w+", resolved_msg.lower()))
            if target_type == "docx":
                sections = m_data.get("sections", [])
                matched_secs = []
                for s in sections:
                    heading = s.get("heading", "")
                    sec_words = set(re.findall(r"\w+", heading.lower()))
                    if query_words & sec_words:
                        matched_secs.append(s)
                if not matched_secs:
                    matched_secs = sections

                for s in matched_secs:
                    sec_text = [f"Section: {s.get('heading', '')}"]
                    for b in s.get("blocks", []):
                        txt = b.get("text", "")
                        sids = b.get("source_ids", [])
                        if txt:
                            sid_str = f" [sources: {sids}]" if sids else ""
                            sec_text.append(f"{txt}{sid_str}")
                    extracted_content.append("\n".join(sec_text))
            else:
                slides = m_data.get("slides", [])
                matched_slides = []
                for sl in slides:
                    title = sl.get("title", "")
                    slide_words = set(re.findall(r"\w+", title.lower()))
                    if query_words & slide_words:
                        matched_slides.append(sl)
                if not matched_slides:
                    matched_slides = slides

                for sl in matched_slides:
                    bullets_txt = [b.get("text", "") for b in sl.get("bullets", []) if b.get("text")]
                    extracted_content.append(f"Slide: {sl.get('title', '')}\nBullets: " + "; ".join(bullets_txt) + f" [sources: {sl.get('source_ids', [])}]")
        except Exception as exc:
            logger.warning(f"Failed to extract sections: {exc}")

        content_blob = "\n\n".join(extracted_content)[:3500]

        prompt = (
            f"DOCUMENT TITLE: {target_title} ({target_type.upper()})\n\n"
            f"DOCUMENT CONTENT:\n{content_blob}\n\n"
            f"USER QUESTION:\n'{resolved_msg}'\n\n"
            "RULES:\n"
            "1. Answer based ONLY on the provided document content above. Do not use external facts.\n"
            "2. Cite the document's own source IDs in brackets (e.g. [1]) for specific facts.\n"
            "3. If the document does not contain the answer, say plainly that the document does not contain this information, and offer to research and add it.\n"
        )

        try:
            reply_text = llm.generate_text(
                prompt,
                system="You are an enterprise AI assistant answering strictly from the existing session document.",
            )
        except Exception as exc:
            reply_text = f"An error occurred while answering from the document: {exc}"

        state["reply"] = reply_text
        state["artifacts"] = []
        state["registry"] = {}
        return state

    # fresh answer scope
    if db_sess:
        db_sess.close()

    session_ctx = state.get("session_context") or {}
    active_topic = session_ctx.get("active_topic", "")
    recent_msgs = session_ctx.get("recent_messages", [])
    ctx_summary = ""
    if active_topic:
        ctx_summary += f"Active Topic: {active_topic}\n"
    if recent_msgs:
        ctx_summary += "Recent Messages:\n" + "\n".join(f"- {m['role']}: {m['content'][:150]}" for m in recent_msgs[-3:]) + "\n"

    use_web = plan_data.get("use_web", True)
    use_kb = plan_data.get("use_kb", True)

    ctx = build_context(resolved_msg, use_web=use_web, use_kb=use_kb)
    evidence_pack = ctx.evidence_pack or build_evidence_pack(ctx.findings, ctx.kb_hits)
    evidence_text = format_evidence_pack(evidence_pack)

    prompt = (
        f"CONTEXT:\n{ctx_summary}\n\n"
        f"USER QUESTION:\n'{resolved_msg}'\n\n"
        f"--- EVIDENCE PACK ---\n{evidence_text}\n\n"
        "GROUNDING RULE:\n"
        "You may only state a specific fact (number, date, statistic, name, claimed event) if it matches an entry in the EVIDENCE PACK below, citing that entry's exact id as source_id. General connective text needs no citation but must contain no invented specific fact. If something isn't covered by the evidence pack, omit it or state it as general knowledge without invented precision — never fabricate a number or date.\n\n"
        "If no evidence pack entry supports an answer, state plainly that the available knowledge base and research do not contain the answer, instead of guessing.\n"
        "Cite sources using [id] brackets."
    )
    try:
        reply_text = llm.generate_text(
            prompt,
            system="You are an enterprise AI assistant answering strictly grounded in the provided evidence pack.",
        )
    except Exception as exc:
        reply_text = f"An error occurred while answering: {exc}"

    state["reply"] = reply_text
    state["registry"] = ctx.sources_map
    state["evidence_pack"] = [e.model_dump() for e in evidence_pack]
    state["artifacts"] = []
    return state


@traced("analyze_templates_node")
def node_analyze_templates(state: GraphState) -> GraphState:
    """Analyze DOCX and PPTX templates to extract layout, style, and tone profiles directly from active Workspace."""
    db_sess = _get_sync_session()
    if not db_sess:
        raise RuntimeError("Sync database session unavailable.")

    try:
        active_ws = (
            db_sess.query(Workspace).filter(Workspace.is_active == True).first()
        )
        if not active_ws:
            try:
                from app.services.workspace_init import init_default_workspace_sync
                active_ws = init_default_workspace_sync(db_sess)
            except Exception as e_init:
                logger.warning(f"Could not auto-initialize workspace: {e_init}")

        if not active_ws:
            raise RuntimeError(
                "No active workspace found. An active workspace must be initialized at startup."
            )

        doc_file = (
            db_sess.query(UploadedFile)
            .filter(UploadedFile.id == active_ws.docx_template_file_id)
            .first()
        )
        ppt_file = (
            db_sess.query(UploadedFile)
            .filter(UploadedFile.id == active_ws.pptx_template_file_id)
            .first()
        )
        if not doc_file or not Path(doc_file.stored_path).exists():
            raise FileNotFoundError(
                f"Active workspace DOCX template file missing: {doc_file.stored_path if doc_file else 'None'}"
            )
        if not ppt_file or not Path(ppt_file.stored_path).exists():
            raise FileNotFoundError(
                f"Active workspace PPTX template file missing: {ppt_file.stored_path if ppt_file else 'None'}"
            )

        doc_tmpl_path = Path(doc_file.stored_path)
        ppt_tmpl_path = Path(ppt_file.stored_path)

        if active_ws.doc_profile_json:
            doc_profile = json.loads(active_ws.doc_profile_json)
        else:
            doc_profile = analyze_document(doc_tmpl_path, file_id=doc_file.id).model_dump()
            active_ws.doc_profile_json = json.dumps(doc_profile)
            db_sess.commit()

        if active_ws.ppt_profile_json:
            ppt_profile = json.loads(active_ws.ppt_profile_json)
        else:
            ppt_profile = analyze_presentation(ppt_tmpl_path, file_id=ppt_file.id).model_dump()
            active_ws.ppt_profile_json = json.dumps(ppt_profile)
            db_sess.commit()

        state["template_profiles"] = {
            "doc_profile": doc_profile,
            "ppt_profile": ppt_profile,
            "doc_template_path": str(doc_tmpl_path),
            "ppt_template_path": str(ppt_tmpl_path),
        }
        return state
    finally:
        db_sess.close()


@traced("gather_context_node")
def node_gather_context(state: GraphState) -> GraphState:
    """Run RAG retrieval and web research sequentially to populate SourceRegistry and EvidencePack."""
    plan_data = state.get("plan", {})
    topic = plan_data.get("topic") or state.get("user_message", "")
    use_web = plan_data.get("use_web", True)
    use_kb = plan_data.get("use_kb", True)

    ctx = build_context(topic, use_web=use_web, use_kb=use_kb)

    state["registry"] = ctx.sources_map
    state["findings"] = [f.model_dump() for f in ctx.findings]
    state["evidence_pack"] = [e.model_dump() for e in ctx.evidence_pack]
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

    if not doc_prof_dict:
        raise RuntimeError("Missing doc_profile in template_profiles; active workspace must be loaded.")
    doc_profile = TemplateProfile.model_validate(doc_prof_dict)

    sources_map = state.get("registry", {})
    sources_list = list(sources_map.values()) if sources_map else []
    evidence_pack = (
        [EvidenceItem.model_validate(e) for e in state.get("evidence_pack", [])]
        if state.get("evidence_pack")
        else None
    )

    # Feedback from previous failed validation retry
    retry_feedback = ""
    validation_data = state.get("validation")
    if validation_data and not validation_data.get("passed"):
        state["retry_count"] = max(state.get("retry_count", 0), 1)
        issues = validation_data.get("issues", [])
        issue_msgs = "; ".join(
            i.get("message", "") for i in issues if "docx" in i.get("where", "")
        )
        if issue_msgs:
            retry_feedback = f"\n\nCRITICAL FIX REQUIRED FROM PREVIOUS RETRY:\nFix the following validation issues: {issue_msgs}"

    doc_type = plan_data.get("document_type", "proposal")
    doc_model = generate_document_model(
        brief=topic + retry_feedback,
        profile=doc_profile,
        sources=sources_list,
        evidence_pack=evidence_pack,
        document_type=doc_type,
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

    if not ppt_prof_dict:
        raise RuntimeError("Missing ppt_profile in template_profiles; active workspace must be loaded.")
    ppt_profile = TemplateProfile.model_validate(ppt_prof_dict)

    sources_map = state.get("registry", {})
    sources_list = list(sources_map.values()) if sources_map else []
    evidence_pack = (
        [EvidenceItem.model_validate(e) for e in state.get("evidence_pack", [])]
        if state.get("evidence_pack")
        else None
    )

    # Feedback from previous failed validation retry
    retry_feedback = ""
    validation_data = state.get("validation")
    if validation_data and not validation_data.get("passed"):
        state["retry_count"] = max(state.get("retry_count", 0), 1)
        issues = validation_data.get("issues", [])
        issue_msgs = "; ".join(
            i.get("message", "") for i in issues if "pptx" in i.get("where", "")
        )
        if issue_msgs:
            retry_feedback = f"\n\nCRITICAL FIX REQUIRED FROM PREVIOUS RETRY:\nFix the following validation issues: {issue_msgs}"

    doc_type = plan_data.get("document_type", "proposal")
    deck_model = generate_deck_model(
        brief=topic + retry_feedback,
        profile=ppt_profile,
        sources=sources_list,
        evidence_pack=evidence_pack,
        slide_count=slide_count,
        document_type=doc_type,
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
    if state.get("evidence_pack"):
        for e in state.get("evidence_pack", []):
            if isinstance(e, dict) and "id" in e:
                valid_sids.add(e["id"])

    doc_model_dict = state.get("doc_model")
    deck_model_dict = state.get("deck_model")

    doc_model = (
        DocumentModel.model_validate(doc_model_dict) if doc_model_dict else None
    )
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
    from app.models.template_profile import TemplateProfile

    profiles = state.get("template_profiles", {})
    db_sess = _get_sync_session()

    doc_model_dict = state.get("doc_model")
    deck_model_dict = state.get("deck_model")

    doc_tmpl_path = None
    doc_profile = None
    if doc_model_dict:
        doc_tmpl_path_str = profiles.get("doc_template_path")
        if not doc_tmpl_path_str and db_sess:
            ws = db_sess.query(Workspace).filter(Workspace.is_active == True).first()
            if ws and ws.docx_template_file_id:
                up_f = db_sess.query(UploadedFile).filter(UploadedFile.id == ws.docx_template_file_id).first()
                if up_f and up_f.stored_path:
                    doc_tmpl_path_str = up_f.stored_path
        if not doc_tmpl_path_str:
            default_docx = Path("data/sample_templates/proposal_Template.docx")
            if default_docx.exists():
                doc_tmpl_path_str = str(default_docx)
        if doc_tmpl_path_str:
            doc_tmpl_path = Path(doc_tmpl_path_str)
        doc_prof_dict = profiles.get("doc_profile")
        if doc_prof_dict:
            doc_profile = TemplateProfile.model_validate(doc_prof_dict)
        elif doc_tmpl_path and doc_tmpl_path.exists():
            from app.agents.doc_analyzer import analyze_document
            doc_profile = analyze_document(doc_tmpl_path)
        else:
            doc_profile = TemplateProfile(colors={"primary": "#000000"})

    ppt_tmpl_path = None
    ppt_profile = None
    if deck_model_dict:
        ppt_tmpl_path_str = profiles.get("ppt_template_path")
        if not ppt_tmpl_path_str and db_sess:
            ws = db_sess.query(Workspace).filter(Workspace.is_active == True).first()
            if ws and ws.pptx_template_file_id:
                up_f = db_sess.query(UploadedFile).filter(UploadedFile.id == ws.pptx_template_file_id).first()
                if up_f and up_f.stored_path:
                    ppt_tmpl_path_str = up_f.stored_path
        if not ppt_tmpl_path_str:
            default_pptx = Path("data/sample_templates/presentation_Template.pptx")
            if default_pptx.exists():
                ppt_tmpl_path_str = str(default_pptx)
        if ppt_tmpl_path_str:
            ppt_tmpl_path = Path(ppt_tmpl_path_str)
        ppt_prof_dict = profiles.get("ppt_profile")
        if ppt_prof_dict:
            ppt_profile = TemplateProfile.model_validate(ppt_prof_dict)
        elif ppt_tmpl_path and ppt_tmpl_path.exists():
            from app.agents.ppt_analyzer import analyze_presentation
            ppt_profile = analyze_presentation(ppt_tmpl_path)
        else:
            ppt_profile = TemplateProfile(colors={"primary": "#000000"})

    sources_map = state.get("registry", {})
    output_dir = Path("data/outputs")
    output_dir.mkdir(parents=True, exist_ok=True)
    artifacts_created: list[dict[str, Any]] = []

    session_id = state.get("session_id")
    run_id = state.get("run_id")

    import datetime
    today_str = datetime.date.today().strftime("%B %Y")
    standard_sources_map = {}
    for sid, s_data in sources_map.items():
        if isinstance(s_data, dict):
            kind = s_data.get("kind", "web")
            u_or_f = s_data.get("url_or_filename") or s_data.get("url", "")
            title = s_data.get("title") or u_or_f or f"Source {sid}"
            accessed = s_data.get("accessed_at", today_str)
            standard_sources_map[str(sid)] = {
                "id": int(sid),
                "title": title,
                "url_or_filename": u_or_f,
                "kind": kind,
                "accessed_at": accessed,
            }
    sources_json_str = json.dumps(standard_sources_map) if standard_sources_map else None
    all_sids_list = [int(sid) for sid in standard_sources_map.keys()]
    sids_json_str = json.dumps(all_sids_list) if all_sids_list else None

    try:
        # 1. Render & Save DOCX
        if doc_model_dict:
            doc_model = DocumentModel.model_validate(doc_model_dict)
            out_docx = output_dir / f"Proposal_{uuid.uuid4().hex[:8]}.docx"
            render_docx(doc_model, doc_tmpl_path, doc_profile, out_docx, sources_map=sources_map)
            
            if db_sess:
                try:
                    doc_title = str(doc_model.title)
                    art = Artifact(
                        artifact_type="docx",
                        title=doc_title,
                        session_id=session_id,
                        run_id=run_id,
                    )
                    db_sess.add(art)
                    db_sess.flush()
                    art_id = art.id

                    ver = ArtifactVersion(
                        artifact_id=art_id,
                        version_no=1,
                        model_json=doc_model.model_dump_json(),
                        file_path=str(out_docx),
                        file_type="docx",
                        change_summary="Initial generation",
                        source_ids_json=sids_json_str,
                        sources_json=sources_json_str,
                    )
                    db_sess.add(ver)
                    db_sess.commit()

                    artifacts_created.append({
                        "kind": "docx",
                        "artifact_id": art_id,
                        "version": 1,
                        "title": doc_title,
                        "download_url": f"/artifacts/{art_id}/download?version=1",
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
                    deck_title = str(deck_model.title)
                    art = Artifact(
                        artifact_type="pptx",
                        title=deck_title,
                        session_id=session_id,
                        run_id=run_id,
                    )
                    db_sess.add(art)
                    db_sess.flush()
                    art_id = art.id

                    ver = ArtifactVersion(
                        artifact_id=art_id,
                        version_no=1,
                        model_json=deck_model.model_dump_json(),
                        file_path=str(out_pptx),
                        file_type="pptx",
                        change_summary="Initial generation",
                        source_ids_json=sids_json_str,
                        sources_json=sources_json_str,
                    )
                    db_sess.add(ver)
                    db_sess.commit()

                    artifacts_created.append({
                        "kind": "pptx",
                        "artifact_id": art_id,
                        "version": 1,
                        "title": deck_title,
                        "download_url": f"/artifacts/{art_id}/download?version=1",
                        "file_path": str(out_pptx),
                    })
                except Exception as exc:
                    logger.warning(f"Failed to record PPTX artifact DB entry: {exc}")
    finally:
        if db_sess:
            db_sess.close()

    # 3. Format user reply with download links & citations summary
    errors = state.get("errors", [])
    if errors and not artifacts_created:
        reply_parts = ["### Generation FAILED\n"]
    elif errors:
        reply_parts = ["### Generation PARTIAL\n"]
    else:
        kinds_str = ", ".join(sorted(set(a["kind"] for a in artifacts_created)))
        first_title = artifacts_created[0]["title"] if artifacts_created else "Report"
        reply_parts = [f"Created NEW report: {first_title} ({kinds_str})\n"]

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


@traced("edit_node")
def node_edit(state: GraphState) -> GraphState:
    """Execute conversational edits on target artifact(s) scoped ONLY to the current session."""
    import re
    user_msg = state.get("user_message", "")
    session_id = state.get("session_id", "")
    plan_data = state.get("plan", {})
    resolved_msg = plan_data.get("resolved_message") or user_msg
    target_ids = plan_data.get("target_artifact_ids", [])
    session_ctx = state.get("session_context") or {}
    active_topic = session_ctx.get("active_topic", "")

    db_sess = _get_sync_session()
    if not db_sess:
        state["reply"] = "Database unavailable for conversational edit."
        return state

    try:
        if not session_id:
            state["reply"] = "No existing artifacts found to edit for this session. Please generate a document or presentation first."
            state["artifacts"] = []
            db_sess.close()
            return state

        # Resolve target artifacts ONLY from the current session (newest first)
        artifacts = (
            db_sess.query(Artifact)
            .filter(Artifact.session_id == session_id)
            .order_by(Artifact.id.desc())
            .all()
        )

        if not artifacts:
            state["reply"] = "No existing artifacts found to edit for this session. Please generate a document or presentation first."
            state["artifacts"] = []
            db_sess.close()
            return state

        # Determine target type from prompt keywords
        msg_lower = resolved_msg.lower()
        target_kind = None
        if any(w in msg_lower for w in ["slide", "presentation", "deck"]):
            target_kind = "pptx"
        elif any(w in msg_lower for w in ["document", "report", "proposal", "doc"]):
            target_kind = "docx"

        # Check if topic addition
        is_topic_addition = plan_data.get("needs_new_facts", False) or bool(
            re.search(r"\b(add|append|include|also|expand|new\s+topic|information\s+about)\b", msg_lower)
        )

        # Resolve target artifacts
        target_arts = []
        if is_topic_addition and not target_kind:
            docx_art = next((a for a in artifacts if a.artifact_type == "docx"), None)
            pptx_art = next((a for a in artifacts if a.artifact_type == "pptx"), None)
            target_arts = [a for a in [docx_art, pptx_art] if a is not None]
        elif target_ids:
            target_arts = [a for a in artifacts if a.id in target_ids]

        if not target_arts:
            if target_kind:
                target_arts = [a for a in artifacts if a.artifact_type == target_kind][:1]
            else:
                docx_art = next((a for a in artifacts if a.artifact_type == "docx"), None)
                pptx_art = next((a for a in artifacts if a.artifact_type == "pptx"), None)
                target_arts = [a for a in [docx_art, pptx_art] if a is not None]

        if not target_arts:
            state["reply"] = "No matching artifact found to edit in this session. Please generate a document or presentation first."
            state["artifacts"] = []
            db_sess.close()
            return state

        target_info = [
            {"id": a.id, "title": str(a.title), "kind": str(a.artifact_type)}
            for a in target_arts
        ]

        if not active_topic:
            active_topic = target_info[0]["title"]

        # Check if addition/refresh requires scoped research (at most 1 research pass per edit)
        use_web = plan_data.get("use_web", True)
        use_kb = plan_data.get("use_kb", True)
        shared_ctx = None
        clean_sub = re.sub(
            r"^(also\s+)?(add\s+a\s+section\s+on|add\s+section\s+on|add\s+|include\s+a\s+section\s+on|include\s+|append\s+)",
            "",
            resolved_msg,
            flags=re.IGNORECASE,
        ).strip()

        if is_topic_addition:
            try:
                max_sids = [0]
                for item in target_info:
                    v_rec = (
                        db_sess.query(ArtifactVersion)
                        .filter(ArtifactVersion.artifact_id == item["id"])
                        .order_by(ArtifactVersion.version_no.desc())
                        .first()
                    )
                    if v_rec and v_rec.source_ids_json:
                        try:
                            sids = json.loads(v_rec.source_ids_json)
                            if sids:
                                max_sids.append(max(sids))
                        except Exception:
                            pass
                start_id = max(max_sids) + 1

                query = (
                    f"{active_topic} {clean_sub}"
                    if (active_topic and active_topic.lower() not in clean_sub.lower())
                    else (clean_sub or resolved_msg)
                )
                shared_ctx = build_context(
                    query,
                    use_web=use_web,
                    use_kb=use_kb,
                    db_session=db_sess,
                    start_id=start_id,
                )
            except Exception as r_exc:
                logger.warning(f"Scoped research failed during edit: {r_exc}")

        edited_artifacts = []
        is_ambiguous = (target_kind is None and not target_ids)
        warning_msg = None

        for item in target_info:
            art_id = item["id"]
            art_kind = item["kind"]
            art_title = item["title"]

            old_ver_rec = (
                db_sess.query(ArtifactVersion)
                .filter(ArtifactVersion.artifact_id == art_id)
                .order_by(ArtifactVersion.version_no.desc())
                .first()
            )
            old_v_no = old_ver_rec.version_no if old_ver_rec else 1

            res = edit_artifact(
                artifact_id=art_id,
                instruction=resolved_msg,
                db_session=db_sess,
                active_topic=active_topic,
                shared_context=shared_ctx,
            )
            if res.no_sources_warning and not warning_msg:
                warning_msg = res.no_sources_warning

            edited_artifacts.append({
                "kind": art_kind,
                "artifact_id": art_id,
                "old_version": old_v_no,
                "version": res.new_version_no,
                "title": art_title,
                "download_url": res.download_url,
                "file_path": res.file_path,
                "summary": res.summary,
                "diff": res.diff,
            })

        # Build transparency output
        try:
            kinds_str = ", ".join(sorted(set(a["kind"] for a in edited_artifacts)))
            v_changes = ", ".join(f"v{a['old_version']} -> v{a['version']}" for a in edited_artifacts)
            first_title = target_info[0]["title"]

            reply_lines = [f"Edited: {first_title} {v_changes} ({kinds_str})\n"]
            if warning_msg:
                reply_lines.append(f"{warning_msg}\n")
            if is_ambiguous:
                reply_lines.append("Treated as an edit of your current report. Say 'create a new report on ...' to start a fresh one.\n")

            for ea in edited_artifacts:
                reply_lines.append(f"**Updated {ea['kind'].upper()} Artifact (v{ea['version']}):**")
                if ea.get("summary"):
                    reply_lines.append(f"- Summary: {ea['summary']}")
                diff = ea.get("diff", {})
                if diff.get("added"):
                    reply_lines.append(f"- Added: {', '.join(diff['added'])}")
                if diff.get("changed"):
                    reply_lines.append(f"- Changed: {', '.join(diff['changed'])}")
                if diff.get("removed"):
                    reply_lines.append(f"- Removed: {', '.join(diff['removed'])}")
                reply_lines.append(f"- [Download Updated File]({ea['download_url']})\n")

            state["reply"] = "\n".join(reply_lines)
        except Exception as reply_exc:
            logger.warning(f"Failed to build full edit reply summary: {reply_exc}")
            if edited_artifacts:
                ea0 = edited_artifacts[0]
                state["reply"] = (
                    f"Edit saved as {ea0.get('title', 'Artifact')} v{ea0.get('version', 2)} (download below), "
                    f"but the summary could not be built."
                )
            else:
                state["reply"] = "Edit could not be completed."

        state["artifacts"] = edited_artifacts
        return state
    except Exception as exc:
        if edited_artifacts:
            ea0 = edited_artifacts[0]
            state["reply"] = (
                f"Edit saved as {ea0.get('title', 'Artifact')} v{ea0.get('version', 2)} (download below), "
                f"but the summary could not be built."
            )
            state["artifacts"] = edited_artifacts
            return state
        raise exc
    finally:
        if db_sess:
            try:
                db_sess.close()
            except Exception:
                pass


@traced("convert_node")
def node_convert(state: GraphState) -> GraphState:
    """Execute bi-directional conversion between DOCX and PPTX scoped to current session."""
    user_msg = state.get("user_message", "")
    session_id = state.get("session_id", "")
    run_id = state.get("run_id", "")
    db_sess = _get_sync_session()

    if not db_sess:
        state["reply"] = "Database unavailable for format conversion."
        return state

    try:
        msg_lower = user_msg.lower()
        if "to ppt" in msg_lower or "to slide" in msg_lower or "to presentation" in msg_lower:
            src_kind = "docx"
            target_kind = "pptx"
        elif "to doc" in msg_lower or "to proposal" in msg_lower or "to report" in msg_lower:
            src_kind = "pptx"
            target_kind = "docx"
        else:
            # Infer from latest artifact in current session
            latest_art = (
                db_sess.query(Artifact)
                .filter(Artifact.session_id == session_id)
                .order_by(Artifact.id.desc())
                .first()
            )
            if latest_art and latest_art.artifact_type == "docx":
                src_kind = "docx"
                target_kind = "pptx"
            else:
                src_kind = "pptx"
                target_kind = "docx"

        src_art = (
            db_sess.query(Artifact)
            .filter(Artifact.session_id == session_id, Artifact.artifact_type == src_kind)
            .order_by(Artifact.id.desc())
            .first()
        )

        if not src_art:
            state["reply"] = f"No {src_kind.upper()} artifact found to convert in this session. Please generate a document or presentation first."
            state["artifacts"] = []
            db_sess.close()
            return state

        src_id = src_art.id
        src_title = str(src_art.title)

        res = convert_artifact(
            artifact_id=src_id,
            target_kind=target_kind,
            db_session=db_sess,
            session_id=session_id,
            run_id=run_id,
        )

        artifacts_created = [{
            "kind": target_kind,
            "artifact_id": res.new_artifact_id,
            "version": res.version_no,
            "title": res.title,
            "download_url": res.download_url,
            "file_path": res.file_path,
        }]

        reply_text = (
            f"### Format Conversion Completed\n\n"
            f"Successfully converted **{src_title}** ({src_kind.upper()}) to **{res.title}** ({target_kind.upper()}).\n"
            f"- [Download Converted {target_kind.upper()}]({res.download_url})"
        )

        state["reply"] = reply_text
        state["artifacts"] = artifacts_created
        return state
    except Exception as exc:
        raise exc
    finally:
        if db_sess:
            try:
                db_sess.close()
            except Exception:
                pass


def route_intent(state: GraphState) -> str:
    """Route plan action to appropriate entry node."""
    plan_data = state.get("plan", {})
    action = plan_data.get("action", "generate")

    if action == "answer":
        return "answer_node"
    elif action == "edit":
        return "edit_node"
    elif action == "convert":
        return "convert_node"
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
    builder.add_node("edit_node", node_edit)
    builder.add_node("convert_node", node_convert)
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
            "edit_node": "edit_node",
            "convert_node": "convert_node",
            "analyze_templates_node": "analyze_templates_node",
        },
    )

    builder.add_edge("stub_node", END)
    builder.add_edge("edit_node", END)
    builder.add_edge("convert_node", END)
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
