"""Session context extraction service for conversational multi-agent continuity."""

from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy.orm import Session

from app.models.artifact import Artifact, ArtifactVersion
from app.models.chat import ChatMessage
from app.models.context import ConversationContext
from app.models.file import UploadedFile
from app.models.run import AgentRun
from app.models.workspace import Workspace

logger = logging.getLogger(__name__)


def build_conversation_context(
    session_id: str,
    db: Session,
) -> ConversationContext:
    """Build strongly typed ConversationContext for a chat session.

    Constructs:
        - recent_messages: chronological window of chat messages
        - artifact_history: session-scoped artifacts with outline & active flags
        - active_artifact: most recent active document or deck
        - active_version: latest version number of active artifact
        - recent_operations: parsed operations from session AgentRuns
        - relevant_sources: citation sources linked to active artifacts
        - active_template: workspace or session template profile metadata
        - current_topic: active topic inferred from session runs/artifacts
        - extracted_requirements: cumulative requirements (e.g. slide_count)
        - conversation_summary: actionable compact summary
    """
    if not session_id or not db:
        return ConversationContext(conversation_id=session_id or "")

    # 1. Fetch recent messages (up to 6, chronological)
    raw_msgs = (
        db.query(ChatMessage)
        .filter(ChatMessage.session_id == session_id)
        .order_by(ChatMessage.id.desc())
        .limit(6)
        .all()
    )
    raw_msgs.reverse()

    recent_messages = [
        {
            "role": m.role,
            "content": (m.content or "")[:400],
        }
        for m in raw_msgs
    ]

    # 2. Fetch session-scoped artifacts
    session_artifacts = (
        db.query(Artifact)
        .filter(Artifact.session_id == session_id)
        .order_by(Artifact.id.asc())
        .all()
    )

    # 2. Fetch session-scoped uploaded files
    session_files = (
        db.query(UploadedFile)
        .filter(UploadedFile.session_id == session_id)
        .order_by(UploadedFile.id.asc())
        .all()
    )
    uploaded_files_data = [
        {
            "id": f.id,
            "filename": f.filename,
            "file_type": f.file_type,
            "purpose": f.purpose or "REFERENCE",
            "stored_path": f.stored_path,
        }
        for f in session_files
    ]

    # 3. Fetch session-scoped artifacts
    session_artifacts = (
        db.query(Artifact)
        .filter(Artifact.session_id == session_id)
        .order_by(Artifact.id.asc())
        .all()
    )

    artifacts_data: list[dict[str, Any]] = []
    latest_docx_id = None
    latest_pptx_id = None
    latest_xlsx_id = None

    # Track newest artifact of each kind to mark active set
    for art in reversed(session_artifacts):
        if art.artifact_type == "docx" and latest_docx_id is None:
            latest_docx_id = art.id
        elif art.artifact_type == "pptx" and latest_pptx_id is None:
            latest_pptx_id = art.id
        elif art.artifact_type == "xlsx" and latest_xlsx_id is None:
            latest_xlsx_id = art.id

    active_art_dict: dict[str, Any] | None = None
    active_version_num: int | None = None
    relevant_sources: list[dict[str, Any]] = []

    for art in session_artifacts:
        versions = (
            db.query(ArtifactVersion)
            .filter(ArtifactVersion.artifact_id == art.id)
            .order_by(ArtifactVersion.version_no.desc())
            .all()
        )
        latest_ver = versions[0] if versions else None
        latest_ver_no = latest_ver.version_no if latest_ver else 1

        outline: list[str] = []
        if latest_ver and latest_ver.model_json:
            try:
                m_data = json.loads(latest_ver.model_json)
                if art.artifact_type == "docx":
                    secs = m_data.get("sections", [])
                    outline = [
                        f"{i+1}. {s.get('heading', '')}"
                        for i, s in enumerate(secs)
                        if s.get("heading")
                    ][:15]
                elif art.artifact_type == "xlsx":
                    sheets = m_data.get("sheets", [])
                    outline = [
                        f"{i+1}. Sheet: {sh.get('name', '')} ({len(sh.get('cells', []))} cells)"
                        for i, sh in enumerate(sheets)
                        if sh.get("name")
                    ][:15]
                else:
                    slides = m_data.get("slides", [])
                    outline = [
                        f"{i+1}. {sl.get('title', '')}"
                        for i, sl in enumerate(slides)
                        if sl.get("title")
                    ][:20]
            except Exception as exc:
                logger.debug(f"Failed to parse model_json for outline: {exc}")

        is_active = (art.id == latest_docx_id) or (art.id == latest_pptx_id) or (art.id == latest_xlsx_id)
        if not session_artifacts or (latest_docx_id is None and latest_pptx_id is None and latest_xlsx_id is None):
            is_active = True

        art_dict = {
            "artifact_id": art.id,
            "kind": art.artifact_type,
            "title": art.title,
            "latest_version": latest_ver_no,
            "outline": outline,
            "active": is_active,
        }
        artifacts_data.append(art_dict)

        if is_active:
            active_art_dict = art_dict
            active_version_num = latest_ver_no
            if latest_ver and getattr(latest_ver, "sources_json", None):
                try:
                    s_map = json.loads(latest_ver.sources_json)
                    if isinstance(s_map, dict):
                        relevant_sources.extend(s_map.values())
                    elif isinstance(s_map, list):
                        relevant_sources.extend(s_map)
                except Exception:
                    pass

    # 4. Active topic & recent operations from session runs
    active_topic = ""
    recent_operations: list[dict[str, Any]] = []
    extracted_reqs: dict[str, Any] = {}
    last_target_nodes: list[str] = []

    runs = (
        db.query(AgentRun)
        .filter(AgentRun.session_id == session_id)
        .order_by(AgentRun.id.desc())
        .limit(10)
        .all()
    )
    for r in runs:
        if r.plan_json:
            try:
                p_data = json.loads(r.plan_json)
                if not last_target_nodes and p_data.get("target_nodes"):
                    last_target_nodes = p_data.get("target_nodes")
                recent_operations.append({
                    "run_id": r.run_id,
                    "action": p_data.get("action"),
                    "operation": p_data.get("operation", p_data.get("action", "").upper()),
                    "topic": p_data.get("topic", ""),
                    "target_artifact_ids": p_data.get("target_artifact_ids", []),
                    "target_nodes": p_data.get("target_nodes", []),
                })
                if not extracted_reqs and p_data.get("slide_count"):
                    extracted_reqs["slide_count"] = p_data.get("slide_count")
                if not extracted_reqs.get("document_type") and p_data.get("document_type"):
                    extracted_reqs["document_type"] = p_data.get("document_type")

                if p_data.get("action") == "generate" and p_data.get("topic") and not active_topic:
                    active_topic = p_data.get("topic", "")
                elif not active_topic and p_data.get("topic"):
                    active_topic = p_data.get("topic", "")
            except Exception:
                pass

    if not active_topic and artifacts_data:
        active_art = next((a for a in artifacts_data if a["active"]), artifacts_data[-1])
        active_topic = active_art["title"]

    # 5. Active Template
    active_template = None
    try:
        active_ws = db.query(Workspace).filter(Workspace.is_active == True).first()
        if active_ws:
            active_template = {
                "workspace_id": active_ws.id,
                "workspace_name": active_ws.name,
                "docx_template_file_id": active_ws.docx_template_file_id,
                "pptx_template_file_id": active_ws.pptx_template_file_id,
            }
    except Exception as exc:
        logger.debug(f"Could not load active workspace template: {exc}")

    # 6. Cap total character budget at ~3000 chars, dropping oldest messages first
    def _context_size() -> int:
        arts_str = json.dumps(artifacts_data)
        msgs_str = json.dumps(recent_messages)
        return len(active_topic) + len(arts_str) + len(msgs_str)

    while recent_messages and _context_size() > 3000:
        recent_messages.pop(0)

    # 7. Build Actionable Conversation Summary
    summary_parts = []
    if active_topic:
        summary_parts.append(f"Active topic: '{active_topic}'.")
    if active_art_dict:
        summary_parts.append(
            f"Active artifact: ID {active_art_dict.get('artifact_id')} ({active_art_dict.get('kind', '').upper()}) "
            f"v{active_art_dict.get('latest_version')} '{active_art_dict.get('title')}'."
        )
    if uploaded_files_data:
        fnames = [f["filename"] for f in uploaded_files_data[:3]]
        summary_parts.append(f"Uploaded files in session: {', '.join(fnames)}.")
    if active_template:
        summary_parts.append(f"Template: active workspace '{active_template.get('workspace_name')}'.")
    if recent_operations:
        last_op = recent_operations[0]
        summary_parts.append(f"Last operation: {last_op.get('operation')} ('{last_op.get('topic')[:50]}').")
    if last_target_nodes:
        summary_parts.append(f"Last targeted nodes: {', '.join(last_target_nodes)}.")
    if extracted_reqs:
        summary_parts.append(f"Requirements: {json.dumps(extracted_reqs)}.")
    conversation_summary = " ".join(summary_parts) if summary_parts else "Fresh conversation session."

    return ConversationContext(
        conversation_id=session_id,
        recent_messages=recent_messages,
        conversation_summary=conversation_summary,
        active_artifact=active_art_dict,
        active_version=active_version_num,
        artifact_history=artifacts_data,
        recent_operations=recent_operations,
        relevant_sources=relevant_sources[:5],
        active_template=active_template,
        current_topic=active_topic,
        extracted_requirements=extracted_reqs,
        unresolved_references=[],
        uploaded_files=uploaded_files_data,
        active_artifact_id=active_art_dict.get("artifact_id") if active_art_dict else None,
        active_version_no=active_version_num,
        last_target_nodes=last_target_nodes,
    )


def build_session_context(
    session_id: str,
    db: Session,
) -> dict[str, Any]:
    """Build session context dict for backwards-compatible consumers."""
    ctx = build_conversation_context(session_id, db)
    return ctx.to_dict()
