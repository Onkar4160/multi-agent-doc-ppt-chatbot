"""Task and ResearchDossierRecord ORM models (DATA_MODELS §2).

A task is one deliverable pursued across turns: its objective and output specification survive follow-ups,
regenerations and format changes. A dossier stores the research behind a task so follow-ups reuse it.
"""

from __future__ import annotations

from sqlalchemy import Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import TimestampMixin


class Task(Base, TimestampMixin):
    """One deliverable requested in a chat session."""

    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(100), index=True, nullable=False)
    owner: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    objective: Mapped[str] = mapped_column(Text, nullable=False)
    output_spec_json: Mapped[str | None] = mapped_column(Text, nullable=True)  # OutputSpec
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="active")  # active | awaiting_clarification | done | failed
    template_file_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_file_ids_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    dossier_id: Mapped[int | None] = mapped_column(Integer, nullable=True)


class ResearchDossierRecord(Base, TimestampMixin):
    """Persisted ``ResearchDossier`` (app/models/evidence.py) for a task."""

    __tablename__ = "research_dossiers"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    task_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    session_id: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    dossier_json: Mapped[str] = mapped_column(Text, nullable=False)
