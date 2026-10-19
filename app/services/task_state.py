"""Task state across turns (PRD FR-CONV-2/8, DATA_MODELS §2 `tasks`).

A task is one deliverable the user pursues over several messages. New tasks and objective changes open a task;
edits, regenerations, questions and retries stay on the active one; a clarification answer completes the task
that was waiting for it. The task keeps the objective and the output specification for follow-ups.
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from app.models.chat import ChatSession
from app.models.interpretation import Interpretation
from app.models.task import Task

OPENS_TASK = {"new_task", "change_objective", "continue_task"}


def active_task(db: Session, session_id: str) -> Task | None:
    return db.query(Task).filter(Task.session_id == session_id).order_by(Task.id.desc()).first()


def apply_interpretation(db: Session, session_id: str, interp: Interpretation) -> Task | None:
    """Open, update or keep the session's task according to the interpretation; returns the task in effect."""
    current = active_task(db, session_id)
    spec = interp.output_spec.model_dump() if interp.output_spec else None
    status = "awaiting_clarification" if interp.needs_clarification else "active"

    if interp.kind in OPENS_TASK or current is None:
        owner = db.query(ChatSession.owner).filter(ChatSession.session_id == session_id).scalar()
        objective = interp.objective
        if interp.kind == "continue_task" and current is not None:
            objective = f"{current.objective} — {interp.objective}"  # same subject, another deliverable
        task = Task(session_id=session_id, owner=owner, objective=objective,
                    output_spec_json=json.dumps(spec) if spec else None, status=status)
        db.add(task)
    else:
        task = current
        if interp.kind == "clarification_answer":
            task.objective = interp.objective
        if spec and interp.kind in ("clarification_answer", "regenerate"):
            task.output_spec_json = json.dumps(_merge(json.loads(task.output_spec_json or "{}"), spec))
        task.status = status
    db.commit()
    db.refresh(task)
    return task


def _merge(old: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    """New values win only where they say something (empty or default fields keep the earlier choice)."""
    merged = dict(old)
    defaults = ("", None, "standard", "en", "numeric", [])  # a tuple: values may be lists (unhashable)
    for key, value in new.items():
        if key == "length":
            if value.get("explicit") or not old.get("length"):
                merged[key] = value
        elif value not in defaults or key not in old:
            merged[key] = value
    return merged
