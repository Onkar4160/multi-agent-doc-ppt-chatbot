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


class UpdateParagraphOp(BaseModel):
    """Update text of a specific paragraph within a section."""

    type: Literal["update_paragraph"] = "update_paragraph"
    heading: str | None = Field(default=None, description="Section heading containing the paragraph.")
    paragraph_index: int = Field(default=1, description="1-based paragraph index to update.")
    new_text: str = Field(description="New text for the paragraph.")
    source_ids: list[int] = Field(default_factory=list, description="Citations.")


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


class UpdateTitleOp(BaseModel):
    """Update title and subtitle of document or deck when scope changes."""

    type: Literal["update_title"] = "update_title"
    title: str = Field(description="New overarching document/deck title.")
    subtitle: str | None = Field(default=None, description="New overarching document/deck subtitle.")


class UpdatePointOp(BaseModel):
    """Rewrite one point (bullet) of a slide; everything else on the slide stays as it is."""

    type: Literal["update_point"] = "update_point"
    slide_index: int = Field(description="1-based slide index.")
    point_index: int = Field(description="1-based index of the point on that slide, as numbered in the outline.")
    new_text: str = Field(description="The point's new text (plain text, no citation markers).")
    source_ids: list[int] | None = Field(default=None, description="Citations; omit to keep the point's own.")


class AddPointOp(BaseModel):
    """Add one point to a slide."""

    type: Literal["add_point"] = "add_point"
    slide_index: int = Field(description="1-based slide index.")
    after_point: int = Field(default=-1, description="Insert after this 1-based point; 0 = first, -1 = last.")
    text: str = Field(description="The new point.")
    source_ids: list[int] = Field(default_factory=list)


class DeletePointOp(BaseModel):
    """Remove one point from a slide."""

    type: Literal["delete_point"] = "delete_point"
    slide_index: int = Field(description="1-based slide index.")
    point_index: int = Field(description="1-based index of the point to remove.")


class UpdateSlideTextOp(BaseModel):
    """Change one text field of a slide (its title, subtitle or speaker notes) and nothing else."""

    type: Literal["update_slide_text"] = "update_slide_text"
    slide_index: int = Field(description="1-based slide index.")
    field: Literal["title", "subtitle", "notes"] = Field(description="Which field to change.")
    new_text: str


class ReplaceTextOp(BaseModel):
    """Replace a phrase wherever it occurs inside one slide or section (a precise wording or figure change)."""

    type: Literal["replace_text"] = "replace_text"
    node_id: str = Field(description="node_id of the slide or section, from the outline.")
    find: str = Field(description="Exact text as it appears in the outline.")
    replace: str = Field(description="Replacement text.")


from app.models.workbook_model import CellModel, SheetModel, TableModel


class AddSheetOp(BaseModel):
    """Add a new worksheet to an XLSX workbook."""

    type: Literal["add_sheet"] = "add_sheet"
    sheet: SheetModel = Field(description="The SheetModel object to insert.")


class UpdateSheetOp(BaseModel):
    """Update tables or cells in an existing worksheet."""

    type: Literal["update_sheet"] = "update_sheet"
    sheet_name: str = Field(description="Target sheet name to update.")
    tables: list[TableModel] = Field(default_factory=list, description="Updated tables.")
    cells: list[CellModel] = Field(default_factory=list, description="Updated cells.")


class UpdateCellOp(BaseModel):
    """Update a specific cell value or formula in a worksheet."""

    type: Literal["update_cell"] = "update_cell"
    sheet_name: str = Field(default="Sheet1", description="Worksheet name.")
    coordinate: str = Field(description="Cell coordinate (e.g. 'A1', 'B5').")
    value: Any = Field(default=None, description="New value.")
    formula: str | None = Field(default=None, description="New formula if applicable.")


class AddRowOp(BaseModel):
    """Append a data row to a table in a worksheet."""

    type: Literal["add_row"] = "add_row"
    sheet_name: str = Field(default="Sheet1", description="Worksheet name.")
    table_name: str = Field(default="Table1", description="Target table name.")
    row_values: list[Any] = Field(description="Data values for the new row.")


class DeleteSheetOp(BaseModel):
    """Delete a worksheet from an XLSX workbook."""

    type: Literal["delete_sheet"] = "delete_sheet"
    sheet_name: str = Field(description="Name of the worksheet to remove.")


Op = Union[
    UpdatePointOp,
    AddPointOp,
    DeletePointOp,
    UpdateSlideTextOp,
    ReplaceTextOp,
    AddSectionOp,
    UpdateSectionOp,
    UpdateParagraphOp,
    DeleteSectionOp,
    AddSlideOp,
    UpdateSlideOp,
    DeleteSlideOp,
    MoveSlideOp,
    CondenseDeckOp,
    RefreshWithWebOp,
    UpdateTitleOp,
    AddSheetOp,
    UpdateSheetOp,
    UpdateCellOp,
    AddRowOp,
    DeleteSheetOp,
]


class EditPlan(BaseModel):
    """Structured plan containing target artifact type, edit operations, and change summary."""

    target: Literal["docx", "pptx", "xlsx", "both"] = Field(description="Target artifact format being modified.")
    ops: list[Op] = Field(default_factory=list, description="List of granular edit operations.")
    summary: str = Field(description="Human-readable summary of the intended changes.")
    scope_node_ids: list[str] = Field(
        default_factory=list,
        description="node_ids of every slide/section/sheet these ops change (from the outline); all others stay identical.",
    )
