"""TemplateProfile ORM model."""

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import TimestampMixin


class TemplateProfile(Base, TimestampMixin):
    """Extracted style profile from an uploaded template file."""

    __tablename__ = "template_profiles"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), nullable=False)
    filename: Mapped[str] = mapped_column(String(500), nullable=False)
    file_type: Mapped[str] = mapped_column(String(20), nullable=False)  # docx|pptx|pdf|image
    profile_json: Mapped[str] = mapped_column(Text, nullable=False)  # serialised TemplateProfileSchema
    template_path: Mapped[str | None] = mapped_column(String(1000), nullable=True)  # path to saved template
