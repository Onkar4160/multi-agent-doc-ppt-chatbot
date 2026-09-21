"""Pydantic v2 Edit Operations schema for conversational document & deck editing."""

from __future__ import annotations

from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, Field

from app.models.deck_model import SlideModel
from app.models.document_model import Block, Section


class AddSectionOp(BaseModel):
    """Add a new section to a DOCX document."""

    type: Literal["add_section"] = "add_section"
    after_heading: str | None = Field(default=None, description="Heading after which to insert the new section.")
    section: Section = Field(description="The Section object to insert.")


class UpdateSectionOp(BaseModel):
    """Update blocks in an existing section of a DOCX document."""

    type: Literal["update_section"] = "update_section"
    heading: str = Field(description="Target heading to match.")
    new_blocks: list[Block] = Field(description="Replacement content blocks.")


class DeleteSectionOp(BaseModel):
    """Delete a section from a DOCX document."""

    type: Literal["delete_section"] = "delete_section"
    heading: str = Field(description="Heading of the section to remove.")


class AddSlideOp(BaseModel):
    """Add a new slide to a PPTX presentation deck."""

    type: Literal["add_slide"] = "add_slide"
    after_index: int | None = Field(default=None, description="1-based slide index after which to insert.")
    slide: SlideModel = Field(description="The SlideModel object to insert.")


class UpdateSlideOp(BaseModel):
    """Update content of an existing slide in a PPTX presentation deck."""

    type: Literal["update_slide"] = "update_slide"
    index: int = Field(description="1-based slide index to update.")
    slide: SlideModel = Field(description="Updated SlideModel object.")


class DeleteSlideOp(BaseModel):
    """Delete a slide from a PPTX presentation deck."""

    type: Literal["delete_slide"] = "delete_slide"
    index: int = Field(description="1-based slide index to remove.")


class MoveSlideOp(BaseModel):
    """Reorder/move a slide from one position to another."""

    type: Literal["move_slide"] = "move_slide"
    from_index: int = Field(description="Current 1-based slide index.")
    to_index: int = Field(description="New 1-based slide index position.")


class CondenseDeckOp(BaseModel):
    """Condense bullet length and count across presentation slides."""

    type: Literal["condense_deck"] = "condense_deck"
    max_bullets: int = Field(default=4, description="Target maximum bullets per slide.")
    max_words_per_bullet: int = Field(default=15, description="Target maximum words per bullet.")


class RefreshWithWebOp(BaseModel):
    """Refresh report content using fresh web research."""

    type: Literal["refresh_with_web"] = "refresh_with_web"
    topic: str = Field(description="Topic for web research refresh.")
    scope: Any = Field(default="all", description="'all' or list of target section headings / slide indexes.")


Op = Union[
    AddSectionOp,
    UpdateSectionOp,
    DeleteSectionOp,
    AddSlideOp,
    UpdateSlideOp,
    DeleteSlideOp,
    MoveSlideOp,
    CondenseDeckOp,
    RefreshWithWebOp,
]


class EditPlan(BaseModel):
    """Structured plan containing target artifact type, edit operations, and change summary."""

    target: Literal["docx", "pptx", "both"] = Field(description="Target artifact format being modified.")
    ops: list[Op] = Field(default_factory=list, description="List of granular edit operations.")
    summary: str = Field(description="Human-readable summary of the intended changes.")
