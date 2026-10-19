"""Owner scoping for sessions, runs, files and artifacts (FR-UX-3, ADR-014).

Records carry the username that created them. Other users' records look missing (404), never forbidden, so their
existence is not revealed. Rows created before ownership existed (owner NULL) stay visible to every user.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError


def username_of(user: Any) -> str | None:
    return getattr(user, "username", None)


def visible(column: Any, user: Any):
    """SQL filter: owned by ``user`` or created before ownership existed."""
    return or_(column == username_of(user), column.is_(None))


def _mine(owner: str | None, user: Any) -> bool:
    return owner is None or owner == username_of(user)


async def require_session(db: AsyncSession, session_id: str, user: Any):
    from app.models.chat import ChatSession

    sess = (await db.execute(select(ChatSession).where(ChatSession.session_id == session_id))).scalar_one_or_none()
    if sess is None or not _mine(sess.owner, user):
        raise NotFoundError(f"Session '{session_id}' not found")
    return sess


async def session_visible(db: AsyncSession, session_id: str | None, user: Any) -> bool:
    """True for sessionless records and for sessions the user may see."""
    if not session_id:
        return True
    from app.models.chat import ChatSession

    sess = (await db.execute(select(ChatSession).where(ChatSession.session_id == session_id))).scalar_one_or_none()
    return sess is None or _mine(sess.owner, user)


async def require_run(db: AsyncSession, run_id: str, user: Any):
    from app.models.run import AgentRun

    run = (await db.execute(select(AgentRun).where(AgentRun.run_id == run_id))).scalar_one_or_none()
    if run is None or not await session_visible(db, run.session_id, user):
        raise NotFoundError(f"Run '{run_id}' not found")
    return run


async def require_artifact(db: AsyncSession, artifact_id: int, user: Any):
    from app.models.artifact import Artifact

    art = await db.get(Artifact, artifact_id)
    if art is None or not _mine(art.owner, user) or not await session_visible(db, art.session_id, user):
        raise NotFoundError(f"Artifact {artifact_id} not found")
    return art


async def require_file(db: AsyncSession, file_id: int, user: Any):
    from app.models.file import UploadedFile

    f = await db.get(UploadedFile, file_id)
    if f is None or not _mine(f.owner, user):
        raise NotFoundError(f"File {file_id} not found")
    return f
