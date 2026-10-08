"""Deterministic Excel (XLSX) renderer converting WorkbookModel to styled .xlsx files."""

from __future__ import annotations

import logging
import uuid
from pathlib import Path
from typing import Any

import openpyxl
from openpyxl.chart import BarChart, LineChart, PieChart, Reference
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from app.core.config import get_settings
from app.models.workbook_model import WorkbookModel

logger = logging.getLogger(__name__)


# ── Styles ─────────────────────────────────────────────────────────────────
HEADER_FILL = PatternFill(start_color="1F497D", end_color="1F497D", fill_type="solid")
HEADER_FONT = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
TITLE_FONT = Font(name="Calibri", size=14, bold=True, color="1F497D")
REGULAR_FONT = Font(name="Calibri", size=11, bold=False, color="000000")
BOLD_FONT = Font(name="Calibri", size=11, bold=True, color="000000")

THIN_SIDE = Side(border_style="thin", color="D9D9D9")
BORDER_THIN = Border(left=THIN_SIDE, right=THIN_SIDE, top=THIN_SIDE, bottom=THIN_SIDE)

ALIGN_LEFT = Alignment(horizontal="left", vertical="center")
ALIGN_RIGHT = Alignment(horizontal="right", vertical="center")
ALIGN_CENTER = Alignment(horizontal="center", vertical="center")


def render_xlsx(model: WorkbookModel, output_path: str | Path | None = None) -> Path:
    """Render a WorkbookModel into a styled Microsoft Excel (.xlsx) file.

    Args:
        model: Structured WorkbookModel instance.
        output_path: Optional explicit output path. Defaults to storage/outputs/<uuid>.xlsx.

    Returns:
        Path to the rendered .xlsx file on disk.
    """
    if output_path is None:
        out_dir = Path(get_settings().storage_dir) / "outputs"
        out_dir.mkdir(parents=True, exist_ok=True)
        dest_path = out_dir / f"workbook_{uuid.uuid4().hex[:8]}.xlsx"
    else:
        dest_path = Path(output_path)
        dest_path.parent.mkdir(parents=True, exist_ok=True)

    wb = openpyxl.Workbook()
    # Remove default sheet if we have custom sheets
    if model.sheets:
        default_sheet = wb.active
        wb.remove(default_sheet)

    sheets_to_process = model.sheets or []

    for sheet_idx, s_model in enumerate(sheets_to_process, start=1):
        sheet_name = s_model.name or f"Sheet{sheet_idx}"
        ws = wb.create_sheet(title=sheet_name[:31])  # Excel limits sheet names to 31 chars
        ws.views.sheetView[0].showGridLines = True

        current_row = 1

        # Sheet Title block
        if sheet_idx == 1 and model.title:
            ws.cell(row=current_row, column=1, value=model.title).font = TITLE_FONT
            current_row += 2

        # 1. Render Tables
        for table in s_model.tables:
            # Table Title / Name
            if table.name and table.name != "Table1":
                ws.cell(row=current_row, column=1, value=table.name).font = BOLD_FONT
                current_row += 1

            start_table_row = current_row

            # Render Table Headers
            if table.headers:
                for col_idx, header_text in enumerate(table.headers, start=1):
                    cell = ws.cell(row=current_row, column=col_idx, value=header_text)
                    cell.fill = HEADER_FILL
                    cell.font = HEADER_FONT
                    cell.alignment = ALIGN_CENTER
                    cell.border = BORDER_THIN
                current_row += 1

            # Render Table Rows
            for row_vals in table.rows:
                for col_idx, val in enumerate(row_vals, start=1):
                    cell = ws.cell(row=current_row, column=col_idx)
                    cell.border = BORDER_THIN

                    # Detect formulas or values
                    if isinstance(val, str) and val.startswith("="):
                        cell.value = val
                        cell.font = BOLD_FONT
                        cell.alignment = ALIGN_RIGHT
                    elif isinstance(val, (int, float)):
                        cell.value = val
                        cell.font = REGULAR_FONT
                        cell.alignment = ALIGN_RIGHT
                        if isinstance(val, float):
                            cell.number_format = "#,##0.00"
                        else:
                            cell.number_format = "#,##0"
                    else:
                        cell.value = str(val) if val is not None else ""
                        cell.font = REGULAR_FONT
                        cell.alignment = ALIGN_LEFT
                current_row += 1

            current_row += 2  # Spacing after table

        # 2. Render Explicit Cells
        for c in s_model.cells:
            cell = ws[c.coordinate]
            if c.formula:
                cell.value = c.formula
            elif c.value is not None:
                cell.value = c.value
            if c.format:
                cell.number_format = c.format
            cell.font = REGULAR_FONT
            cell.border = BORDER_THIN

        # 3. Render Charts
        for chart_model in s_model.charts:
            try:
                if chart_model.chart_type == "bar":
                    chart = BarChart()
                    chart.type = "bar"
                elif chart_model.chart_type == "line":
                    chart = LineChart()
                elif chart_model.chart_type == "pie":
                    chart = PieChart()
                else:
                    chart = BarChart()
                    chart.type = "col"

                chart.title = chart_model.title or "Chart"
                chart.style = 10

                # If data_range is given like 'B2:B10', parse
                range_str = chart_model.data_range
                if "!" in range_str:
                    range_str = range_str.split("!")[-1]
                range_clean = range_str.replace("$", "")
                if ":" in range_clean:
                    start_cell, end_cell = range_clean.split(":")
                    chart_data = Reference(ws, range_string=f"{sheet_name}!{range_clean}")
                    chart.add_data(chart_data, titles_from_data=False)
                    ws.add_chart(chart, f"E{current_row}")
                    current_row += 15
            except Exception as chart_exc:
                logger.warning(f"Failed to add chart '{chart_model.title}': {chart_exc}")

        # 4. Auto-fit column widths
        for col in ws.columns:
            max_len = 0
            col_letter = get_column_letter(col[0].column)
            for cell in col:
                val_str = str(cell.value or "")
                if len(val_str) > max_len:
                    max_len = len(val_str)
            ws.column_dimensions[col_letter].width = max(max_len + 4, 12)

    # Save workbook
    wb.save(str(dest_path))
    logger.info("Successfully rendered XLSX workbook to %s", dest_path)
    return dest_path
