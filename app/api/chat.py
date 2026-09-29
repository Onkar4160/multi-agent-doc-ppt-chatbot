"""Chat, Runs, Traces, and Sessions API Router."""

from __future__ import annotations

import json
import logging
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
from app.models.chat import ChatMessage, ChatSession
from app.models.run import AgentRun
from app.models.trace import AgentTrace

logger = logging.getLogger(__name__)

router = APIRouter(tags=["chat_and_runs"])


class ChatRequest(BaseModel):
    """Payload for POST /chat."""
    message: str = Field(description="User prompt or instruction.")
    file_ids: list[int] = Field(default_factory=list, description="List of uploaded file IDs to include as context.")
    session_id: str | None = Field(default=None, description="Optional existing session ID.")


def _run_graph_background(run_id: str, session_id: str, user_message: str, file_ids: list[int]) -> None:
    """Background task runner for executing the LangGraph pipeline."""
    from app.agents.graph import _get_sync_session

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
    }

    try:
        final_state = graph_app.invoke(initial_state)
        status_str = "done"
    except Exception as exc:
        logger.error(f"Graph invocation error for run_id {run_id}: {exc}", exc_info=True)
        final_state = initial_state
        final_state["reply"] = f"An unexpected error occurred during execution: {exc}"
        status_str = "failed"

    db_sess = _get_sync_session()
    if db_sess:
        try:
            # Update AgentRun record
            run_rec = db_sess.query(AgentRun).filter(AgentRun.run_id == run_id).first()
            if run_rec:
                run_rec.status = status_str
                run_rec.plan_json = json.dumps(final_state.get("plan")) if final_state.get("plan") else None
                run_rec.result_json = json.dumps({
                    "reply": final_state.get("reply", ""),
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
                content=final_state.get("reply", ""),
                run_id=run_id,
                meta_json=json.dumps(meta_dict),
            )
            db_sess.add(assistant_msg)
            db_sess.commit()
        except Exception as db_exc:
            logger.warning(f"Failed to update AgentRun DB state in background runner: {db_exc}")
        finally:
            try:
                db_sess.close()
            except Exception:
                pass


@router.post("/chat", status_code=status.HTTP_202_ACCEPTED)
async def create_chat_run(
    body: ChatRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """Submit user message to start an asynchronous LangGraph execution run."""
    run_id = f"run_{uuid.uuid4().hex[:10]}"
    session_id = body.session_id or f"sess_{uuid.uuid4().hex[:10]}"

    # 1. Resolve/create ChatSession
    stmt = select(ChatSession).where(ChatSession.session_id == session_id)
    res = await db.execute(stmt)
    sess_rec = res.scalar_one_or_none()
    if not sess_rec:
        sess_rec = ChatSession(session_id=session_id, title=body.message[:50])
        db.add(sess_rec)

    # 2. Record user ChatMessage
    user_msg = ChatMessage(
        session_id=session_id,
        role="user",
        content=body.message,
        run_id=run_id,
    )
    db.add(user_msg)

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
        user_message=body.message,
        file_ids=body.file_ids,
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
    stmt = select(AgentRun).where(AgentRun.run_id == run_id)
    res = await db.execute(stmt)
    run_rec = res.scalar_one_or_none()

    if not run_rec:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found")

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


@router.get("/sessions/{session_id}/messages")
async def get_session_messages(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """Retrieve message history for a chat session."""
    s_stmt = select(ChatSession).where(ChatSession.session_id == session_id)
    s_res = await db.execute(s_stmt)
    if not s_res.scalar_one_or_none():
        raise HTTPException(status_code=404, detail=f"Session '{session_id}' not found")

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
    s_stmt = select(ChatSession).where(ChatSession.session_id == session_id)
    s_res = await db.execute(s_stmt)
    if not s_res.scalar_one_or_none():
        raise HTTPException(status_code=404, detail=f"Session '{session_id}' not found")

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

