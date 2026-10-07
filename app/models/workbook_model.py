"""Pydantic v2 content model for structured spreadsheet workbook generation (XLSX)."""

from __future__ import annotations

from typing import Any, Literal
from pydantic import BaseModel, Field


class CellModel(BaseModel):
    """A single cell in a worksheet."""

    node_id: str | None = None
    coordinate: str = Field(description="Cell coordinate (e.g. 'A1', 'B5').")
    value: Any = Field(default=None, description="Cell raw value or text.")
    formula: str | None = Field(default=None, description="Formula if applicable (e.g. '=SUM(B2:B10)').")
    data_type: Literal["string", "number", "boolean", "date", "formula", "empty"] = "string"
    format: str | None = Field(default=None, description="Number or currency format string.")


class TableModel(BaseModel):
    """A tabular data region in a worksheet."""

    node_id: str | None = None
    name: str = Field(default="Table1", description="Table name.")
    range: str = Field(description="Cell range (e.g. 'A1:D10').")
    headers: list[str] = Field(default_factory=list, description="Column headers.")
    rows: list[list[Any]] = Field(default_factory=list, description="Data row values.")


class ChartModel(BaseModel):
    """A chart embedded in a worksheet."""

    node_id: str | None = None
    title: str = Field(default="", description="Chart title.")
    chart_type: Literal["bar", "line", "pie", "column", "area", "scatter"] = "column"
    data_range: str = Field(description="Data range reference (e.g. 'Sheet1!$B$2:$B$10').")
    categories_range: str | None = Field(default=None, description="Category label range.")


class SheetModel(BaseModel):
    """A single sheet within an Excel workbook."""

    node_id: str | None = None
    name: str = Field(default="Sheet1", description="Sheet tab name.")
    cells: list[CellModel] = Field(default_factory=list, description="Explicit cell overrides.")
    tables: list[TableModel] = Field(default_factory=list, description="Structured tables on this sheet.")
    charts: list[ChartModel] = Field(default_factory=list, description="Embedded charts on this sheet.")


class WorkbookModel(BaseModel):
    """Root structured content model for spreadsheet workbook generation."""

    node_id: str | None = None
    title: str = Field(default="Workbook", description="Workbook title.")
    sheets: list[SheetModel] = Field(default_factory=list, description="Worksheets in this workbook.")
