"""Chat, Runs, Traces, and Sessions API Router."""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
import uuid
from typing import Any, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.graph import graph_app
from app.agents.state import GraphState
from app.api.deps import get_current_user, get_db
from app.core.config import get_settings
from app.core.errors import NotFoundError
from app.core.logging import run_context
from app.llm.usage import track_run
from app.core.ownership import require_run, require_session, username_of, visible
from app.models.chat import ChatMessage, ChatSession
from app.models.run import AgentRun
from app.models.trace import AgentTrace

logger = logging.getLogger(__name__)

router = APIRouter(tags=["chat_and_runs"])


class ChatRequest(BaseModel):
    """Payload for POST /chat."""
    message: str = Field(default="", description="User prompt or instruction (may be empty when files are attached).")
    file_ids: list[int] = Field(default_factory=list, description="List of uploaded file IDs to include as context.")
    session_id: str | None = Field(default=None, description="Optional existing session ID.")


_RETRY = re.compile(
    r"^\W*(please\s+)?(try\s+(it\s+|that\s+)?again|retry|do\s+it\s+again|one\s+more\s+time)(\s+please)?\W*$",
    re.IGNORECASE,
)


def _store_reply_only(session_id: str, run_id: str, reply: str) -> None:
    """Last resort when the full result could not be saved: the user still gets a reply."""
    from app.agents.graph import _get_sync_session

    db_sess = _get_sync_session()
    if not db_sess:
        return
    try:
        db_sess.add(ChatMessage(session_id=session_id, role="assistant", content=reply, run_id=run_id))
        run_rec = db_sess.query(AgentRun).filter(AgentRun.run_id == run_id).first()
        if run_rec and run_rec.status == "running":
            run_rec.status = "failed"
            run_rec.finished_at = datetime.utcnow()
        db_sess.commit()
    except Exception as exc:
        logger.error(f"Could not store reply for run {run_id}: {exc}")
    finally:
        db_sess.close()


def _run_graph_background(
    run_id: str,
    session_id: str,
    user_message: str,
    file_ids: list[int],
    kind_hint: str | None = None,
    pending_clarification: str | None = None,
    raw_message: str | None = None,
) -> None:
    """Background task runner for executing the LangGraph pipeline."""
    from app.agents.graph import _get_sync_session, ensure_reply
    from app.llm.errors import friendly_error

    initial_state: GraphState = {
        "run_id": run_id,
        "session_id": session_id,
        "user_message": user_message,
        "file_ids": file_ids,
        "artifacts": [],
        "errors": [],
        "retry_count": 0,
        "findings": [],
        "template_profiles": {},
        "kind_hint": kind_hint,
        "pending_clarification": pending_clarification,
        "raw_message": raw_message or user_message,
    }

    with run_context(run_id), track_run(get_settings().llm_max_calls_per_run) as usage:
        try:
            final_state = graph_app.invoke(initial_state)
            failed = bool(final_state.get("failure")) and not final_state.get("artifacts")
            status_str = "failed" if failed else "done"
        except Exception as exc:
            logger.error(f"Graph invocation error for run_id {run_id}: {exc}", exc_info=True)
            final_state = dict(initial_state)
            final_state["reply"] = friendly_error(exc, "finish that request")
            status_str = "failed"
        reply = ensure_reply(final_state)
        logger.info("Run finished: status=%s llm_calls=%d cache_hits=%d", status_str, usage.calls, usage.cache_hits)

    db_sess = _get_sync_session()
    if db_sess:
        try:
            # Update AgentRun record
            run_rec = db_sess.query(AgentRun).filter(AgentRun.run_id == run_id).first()
            if run_rec:
                run_rec.status = status_str
                run_rec.plan_json = json.dumps(final_state.get("plan")) if final_state.get("plan") else None
                run_rec.result_json = json.dumps({
                    "reply": reply,
                    "request": user_message,  # what "try again" re-runs
                    "clarification_for": final_state.get("clarification_for"),
                    "usage": usage.as_dict(),
                    "interpretation": final_state.get("interpretation"),
                    "research": final_state.get("dossier"),
                    "plans": final_state.get("content_plans"),
                    "plan_reports": final_state.get("plan_reports"),
                    "file_ids": file_ids,
                    "artifacts": final_state.get("artifacts", []),
                    "citations": final_state.get("registry", {}),
                    "validation": final_state.get("validation"),
                    "errors": final_state.get("errors", []),
                })
                run_rec.finished_at = datetime.utcnow()

            # Create assistant ChatMessage record with meta_json
            arts = final_state.get("artifacts", [])
            meta_dict = {
                "artifacts": [
                    {
                        "artifact_id": a.get("artifact_id"),
                        "version": a.get("version"),
                        "kind": a.get("kind"),
                    }
                    for a in arts
                ]
            }
            assistant_msg = ChatMessage(
                session_id=session_id,
                role="assistant",
                content=reply,
                run_id=run_id,
                meta_json=json.dumps(meta_dict),
            )
            db_sess.add(assistant_msg)
            db_sess.commit()
        except Exception as db_exc:
            logger.warning(f"Failed to update AgentRun DB state in background runner: {db_exc}")
            try:
                db_sess.rollback()
            except Exception:
                pass
            _store_reply_only(session_id, run_id, reply)
        finally:
            try:
                db_sess.close()
            except Exception:
                pass


async def _pending_clarification(db: AsyncSession, session_id: str) -> str | None:
    """The request the assistant asked about in the session's latest run, if it asked a question."""
    stmt = select(AgentRun).where(AgentRun.session_id == session_id).order_by(AgentRun.id.desc()).limit(1)
    last = (await db.execute(stmt)).scalar_one_or_none()
    data = json.loads(last.result_json) if last and last.result_json else {}
    return data.get("clarification_for") or None


async def _failed_request(db: AsyncSession, session_id: str) -> tuple[str, list[int]] | None:
    """Request and files of the session's latest run when that run failed (what "try again" retries)."""
    stmt = (
        select(AgentRun)
        .where(AgentRun.session_id == session_id)
        .order_by(AgentRun.id.desc())
        .limit(1)
    )
    last = (await db.execute(stmt)).scalar_one_or_none()
    if last is None or last.status != "failed":
        return None
    data = json.loads(last.result_json) if last.result_json else {}
    request = data.get("request")
    if not request:
        msg_stmt = select(ChatMessage).where(ChatMessage.run_id == last.run_id, ChatMessage.role == "user")
        msg = (await db.execute(msg_stmt)).scalars().first()
        request = msg.content if msg else None
    return (request, list(data.get("file_ids") or [])) if request else None


async def _attachment_reply(db: AsyncSession, file_ids: list[int]) -> str:
    """Reply to files sent without a message: confirm what arrived and ask what to do with it."""
    from app.models.file import UploadedFile

    rows = (await db.execute(select(UploadedFile).where(UploadedFile.id.in_(file_ids)))).scalars().all()
    names = ", ".join(f"**{f.filename}**" for f in rows) or f"{len(file_ids)} file(s)"
    return (
        f"Got {names}. What should I do with it? For example: “use this as the template for a 10-slide deck "
        f"on …”, “summarise this file”, or “turn this data into a report”."
    )


@router.post("/chat", status_code=status.HTTP_202_ACCEPTED)
async def create_chat_run(
    body: ChatRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """Submit user message to start an asynchronous LangGraph execution run."""
    text = body.message.strip()
    if not text and not body.file_ids:
        raise HTTPException(status_code=422, detail="Type a message or attach a file.")
    run_id = f"run_{uuid.uuid4().hex[:10]}"
    session_id = body.session_id or f"sess_{uuid.uuid4().hex[:10]}"

    # "try again" re-runs the session's failed request rather than planning the words "try again".
    # Another user's session looks missing; nothing below may read it.
    existing_sess = (await db.execute(select(ChatSession).where(ChatSession.session_id == session_id))).scalar_one_or_none()
    if existing_sess is not None and existing_sess.owner not in (None, username_of(user)):
        raise NotFoundError(f"Session '{session_id}' not found")

    request, file_ids = text, list(body.file_ids)
    kind_hint, pending = None, None
    if _RETRY.match(text) and body.session_id:
        failed = await _failed_request(db, session_id)
        if failed:
            request, prior_files = failed
            file_ids = file_ids or prior_files
            kind_hint = "retry"
    elif body.session_id:
        # An answer to the assistant's clarification question completes the earlier request.
        asked = await _pending_clarification(db, session_id)
        if asked:
            request = f"{asked} (meaning: {text})"
            kind_hint, pending = "clarification_answer", asked

    # 1. Resolve/create ChatSession
    stmt = select(ChatSession).where(ChatSession.session_id == session_id)
    res = await db.execute(stmt)
    sess_rec = res.scalar_one_or_none()
    if not sess_rec:
        sess_rec = ChatSession(session_id=session_id, title=(text or "Attached files")[:50], owner=username_of(user))
        db.add(sess_rec)

    # 2. Record user ChatMessage
    user_msg = ChatMessage(
        session_id=session_id,
        role="user",
        content=text or f"(attached {len(body.file_ids)} file(s))",
        run_id=run_id,
    )
    db.add(user_msg)

    # Files without a message: answer right away, no generation run.
    if not text:
        reply = await _attachment_reply(db, body.file_ids)
        db.add(ChatMessage(session_id=session_id, role="assistant", content=reply, run_id=run_id))
        db.add(AgentRun(
            run_id=run_id,
            session_id=session_id,
            status="done",
            result_json=json.dumps({"reply": reply, "artifacts": [], "errors": []}),
            finished_at=datetime.utcnow(),
        ))
        await db.commit()
        return {"run_id": run_id, "session_id": session_id, "status": "done", "reply": reply}

    # 3. Create AgentRun record
    run_rec = AgentRun(
        run_id=run_id,
        session_id=session_id,
        status="running",
    )
    db.add(run_rec)
    await db.commit()

    # 4. Schedule graph execution in background task
    background_tasks.add_task(
        _run_graph_background,
        run_id=run_id,
        session_id=session_id,
        user_message=request,
        file_ids=file_ids,
        kind_hint=kind_hint,
        pending_clarification=pending,
        raw_message=text,
    )

    return {
        "run_id": run_id,
        "session_id": session_id,
        "status": "running",
    }


@router.get("/runs/{run_id}")
async def get_run_status(
    run_id: str,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """Retrieve status, trace steps, results, and artifacts for a run."""
    run_rec = await require_run(db, run_id, user)

    # Retrieve trace steps so far
    t_stmt = select(AgentTrace).where(AgentTrace.trace_id == run_id).order_by(AgentTrace.id.asc())
    t_res = await db.execute(t_stmt)
    traces = t_res.scalars().all()

    trace_steps = [
        {
            "agent_name": t.agent_name,
            "status": t.status,
            "duration_ms": t.duration_ms,
            "input_summary": t.input_summary,
            "output_summary": t.output_summary,
        }
        for t in traces
    ]

    result_data = json.loads(run_rec.result_json) if run_rec.result_json else {}

    return {
        "run_id": run_rec.run_id,
        "session_id": run_rec.session_id,
        "status": run_rec.status,
        "started_at": run_rec.started_at.isoformat() if run_rec.started_at else None,
        "finished_at": run_rec.finished_at.isoformat() if run_rec.finished_at else None,
        "trace_steps": trace_steps,
        "reply": result_data.get("reply", ""),
        "artifacts": result_data.get("artifacts", []),
        "citations": result_data.get("citations", {}),
        "validation_report": result_data.get("validation"),
        "errors": result_data.get("errors", []),
    }


@router.get("/runs/{run_id}/trace")
async def get_run_trace_rows(
    run_id: str,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """Retrieve raw AgentTrace rows for a run ID."""
    await require_run(db, run_id, user)
    stmt = select(AgentTrace).where(AgentTrace.trace_id == run_id).order_by(AgentTrace.id.asc())
    res = await db.execute(stmt)
    traces = res.scalars().all()
    return [
        {
            "id": t.id,
            "trace_id": t.trace_id,
            "agent_name": t.agent_name,
            "status": t.status,
            "duration_ms": t.duration_ms,
            "input_summary": t.input_summary,
            "output_summary": t.output_summary,
            "created_at": t.created_at.isoformat() if t.created_at else None,
        }
        for t in traces
    ]


@router.get("/sessions")
async def list_sessions(
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """Chat history for the sidebar: sessions with their title, most recent activity first."""
    from sqlalchemy import func

    last = (
        select(ChatMessage.session_id, func.max(ChatMessage.id).label("last_id"), func.count(ChatMessage.id).label("n"))
        .group_by(ChatMessage.session_id)
        .subquery()
    )
    stmt = (
        select(ChatSession, last.c.n, ChatMessage.created_at)
        .join(last, last.c.session_id == ChatSession.session_id)
        .join(ChatMessage, ChatMessage.id == last.c.last_id)
        .where(visible(ChatSession.owner, user))
        .order_by(last.c.last_id.desc())
        .limit(limit)
    )
    rows = (await db.execute(stmt)).all()
    return [
        {
            "session_id": s.session_id,
            "title": s.title or "Untitled chat",
            "message_count": n,
            "last_activity": at.isoformat() if at else None,
        }
        for s, n, at in rows
    ]


@router.get("/sessions/{session_id}/messages")
async def get_session_messages(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """Retrieve message history for a chat session."""
    await require_session(db, session_id, user)

    stmt = select(ChatMessage).where(ChatMessage.session_id == session_id).order_by(ChatMessage.id.asc())
    res = await db.execute(stmt)
    messages = res.scalars().all()
    return [
        {
            "id": m.id,
            "session_id": m.session_id,
            "role": m.role,
            "content": m.content,
            "run_id": m.run_id,
            "meta_json": m.meta_json,
            "created_at": m.created_at.isoformat() if m.created_at else None,
        }
        for m in messages
    ]


@router.get("/sessions/{session_id}/artifacts")
async def get_session_artifacts(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """Retrieve artifacts and their versions for a chat session."""
    await require_session(db, session_id, user)

    from app.models.artifact import Artifact, ArtifactVersion

    art_stmt = select(Artifact).where(Artifact.session_id == session_id).order_by(Artifact.id.asc())
    art_res = await db.execute(art_stmt)
    artifacts = art_res.scalars().all()

    output = []
    for a in artifacts:
        v_stmt = select(ArtifactVersion).where(ArtifactVersion.artifact_id == a.id).order_by(ArtifactVersion.version_no.asc())
        v_res = await db.execute(v_stmt)
        vers = v_res.scalars().all()
        latest_ver = vers[-1].version_no if vers else 1

        v_list = []
        for v in vers:
            diff_val = json.loads(v.diff_json) if getattr(v, "diff_json", None) else []
            v_list.append({
                "version_no": v.version_no,
                "file_type": v.file_type,
                "change_summary": v.change_summary,
                "diff": diff_val,
                "created_at": v.created_at.isoformat() if v.created_at else None,
                "download_url": f"/artifacts/{a.id}/download?version={v.version_no}",
            })

        output.append({
            "id": a.id,
            "title": a.title,
            "artifact_type": a.artifact_type,
            "session_id": a.session_id,
            "run_id": a.run_id,
            "latest_version": latest_ver,
            "versions": v_list,
            "created_at": a.created_at.isoformat() if a.created_at else None,
        })

    return output

