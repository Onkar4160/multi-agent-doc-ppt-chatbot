"""Artifact and ArtifactVersion ORM models – generated output files and version chain."""

from __future__ import annotations

from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import TimestampMixin


class Artifact(Base, TimestampMixin):
    """Container for a generated document or presentation project."""

    __tablename__ = "artifacts"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    artifact_type: Mapped[str] = mapped_column(String(10), nullable=False)  # docx | pptx


class ArtifactVersion(Base, TimestampMixin):
    """An immutable snapshot of a generated document or presentation."""

    __tablename__ = "artifact_versions"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    artifact_id: Mapped[int | None] = mapped_column(ForeignKey("artifacts.id"), nullable=True)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id"), nullable=True)
    version_no: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    model_json: Mapped[str] = mapped_column(Text, nullable=False)  # DocumentModel / DeckModel
    file_path: Mapped[str] = mapped_column(String(1000), nullable=False)
    file_type: Mapped[str] = mapped_column(String(10), nullable=False)  # docx | pptx
    parent_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("artifact_versions.id"), nullable=True
    )
    change_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_ids_json: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON list of Source ids
