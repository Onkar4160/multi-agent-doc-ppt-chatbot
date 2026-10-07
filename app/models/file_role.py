"""File role classification models for attached files."""

from __future__ import annotations

from enum import Enum
from pydantic import BaseModel, Field


class FileRole(str, Enum):
    """Semantic role of an attached file in relation to the conversation."""

    TEMPLATE = "TEMPLATE"
    SOURCE = "SOURCE"
    REFERENCE = "REFERENCE"
    INPUT_DOCUMENT = "INPUT_DOCUMENT"
    IMAGE = "IMAGE"
    RESEARCH_MATERIAL = "RESEARCH_MATERIAL"
    EXISTING_ARTIFACT = "EXISTING_ARTIFACT"


class FileRoleClassification(BaseModel):
    """Structured classification result for an attached file."""

    file_id: int | str = Field(description="Unique file ID.")
    filename: str = Field(description="Original filename.")
    file_type: str = Field(default="", description="File extension / type (docx, pdf, pptx, etc.).")
    role: FileRole = Field(description="Classified role: TEMPLATE, SOURCE, REFERENCE, etc.")
    confidence: float = Field(default=1.0, ge=0.0, le=1.0, description="Classification confidence score.")
    reason: str = Field(default="", description="Reasoning behind role assignment.")
    is_ambiguous: bool = Field(default=False, description="Whether this file role is ambiguous among candidates.")
