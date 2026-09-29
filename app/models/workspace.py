"""Workspace ORM model – persistent per-company template profile and branding."""

from __future__ import annotations

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import TimestampMixin


class Workspace(Base, TimestampMixin):
    """Represents a persistent company workspace storing analyzed templates and branding."""

    __tablename__ = "workspaces"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    docx_template_file_id: Mapped[int | None] = mapped_column(
        ForeignKey("uploaded_files.id"), nullable=True
    )
    pptx_template_file_id: Mapped[int | None] = mapped_column(
        ForeignKey("uploaded_files.id"), nullable=True
    )
    doc_profile_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    ppt_profile_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    logo_path: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
