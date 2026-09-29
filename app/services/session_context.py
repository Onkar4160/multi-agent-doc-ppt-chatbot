"""Session context extraction service for conversational multi-agent continuity."""

from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy.orm import Session

from app.models.artifact import Artifact, ArtifactVersion
from app.models.chat import ChatMessage
from app.models.run import AgentRun

logger = logging.getLogger(__name__)


def build_session_context(
    session_id: str,
    db: Session,
) -> dict[str, Any]:
    """Build session context for a chat session without making any LLM calls.

    Returns:
        recent_messages: the last 6 messages (role, content cut to 400 chars)
        artifacts: for this session only, each with artifact_id, kind, title,
                   latest version, short outline (headings or slide titles with indexes),
                   and an 'active' flag for the most recently created or edited pair
        active_topic: the topic from the last generate plan in this session (AgentRun.plan_json)
    Total character count is capped at ~3000 chars by dropping oldest messages first.
    """
    if not session_id or not db:
        return {
            "recent_messages": [],
            "artifacts": [],
            "active_topic": "",
        }

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

    artifacts_data: list[dict[str, Any]] = []
    latest_docx_id = None
    latest_pptx_id = None

    # Track newest artifact of each kind to mark active pair
    for art in reversed(session_artifacts):
        if art.artifact_type == "docx" and latest_docx_id is None:
            latest_docx_id = art.id
        elif art.artifact_type == "pptx" and latest_pptx_id is None:
            latest_pptx_id = art.id

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
                else:
                    slides = m_data.get("slides", [])
                    outline = [
                        f"{i+1}. {sl.get('title', '')}"
                        for i, sl in enumerate(slides)
                        if sl.get("title")
                    ][:20]
            except Exception as exc:
                logger.debug(f"Failed to parse model_json for outline: {exc}")

        is_active = (art.id == latest_docx_id) or (art.id == latest_pptx_id)
        if not session_artifacts or (latest_docx_id is None and latest_pptx_id is None):
            is_active = True

        artifacts_data.append({
            "artifact_id": art.id,
            "kind": art.artifact_type,
            "title": art.title,
            "latest_version": latest_ver_no,
            "outline": outline,
            "active": is_active,
        })

    # 3. Active topic from the last generate plan in this session
    active_topic = ""
    runs = (
        db.query(AgentRun)
        .filter(AgentRun.session_id == session_id)
        .order_by(AgentRun.id.desc())
        .all()
    )
    for r in runs:
        if r.plan_json:
            try:
                p_data = json.loads(r.plan_json)
                if p_data.get("action") == "generate" and p_data.get("topic"):
                    active_topic = p_data.get("topic", "")
                    break
                elif not active_topic and p_data.get("topic"):
                    active_topic = p_data.get("topic", "")
            except Exception:
                pass

    if not active_topic and artifacts_data:
        # Fallback to first active artifact title
        active_art = next((a for a in artifacts_data if a["active"]), artifacts_data[-1])
        active_topic = active_art["title"]

    # 4. Cap total character budget at ~3000 chars, dropping oldest messages first
    def _context_size() -> int:
        arts_str = json.dumps(artifacts_data)
        msgs_str = json.dumps(recent_messages)
        return len(active_topic) + len(arts_str) + len(msgs_str)

    while recent_messages and _context_size() > 3000:
        recent_messages.pop(0)

    return {
        "recent_messages": recent_messages,
        "artifacts": artifacts_data,
        "active_topic": active_topic,
    }
