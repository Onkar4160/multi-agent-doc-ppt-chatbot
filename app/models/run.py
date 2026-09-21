"""AgentRun ORM model for tracking graph execution runs."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import TimestampMixin


class AgentRun(Base, TimestampMixin):
    """Database record for tracking one execution run of the LangGraph multi-agent pipeline."""

    __tablename__ = "agent_runs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(100), unique=True, index=True, nullable=False)
    session_id: Mapped[str | None] = mapped_column(String(100), index=True, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="running")  # running | done | failed
    plan_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    result_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
