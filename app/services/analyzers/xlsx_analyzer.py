"""Analyzer for spreadsheet workbooks (XLSX)."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import openpyxl

logger = logging.getLogger(__name__)


def analyze_xlsx(path: str | Path) -> dict[str, Any]:
    """Inspect structure, sheets, tables, and dimensions of an Excel workbook."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"XLSX file not found: {path}")

    try:
        wb = openpyxl.load_workbook(str(p), data_only=False)
    except Exception as exc:
        raise ValueError(f"Corrupt or invalid XLSX file '{p.name}': {exc}") from exc

    sheets_info: list[dict[str, Any]] = []
    total_formulas = 0

    for sheet_name in wb.sheetnames:
        ws = wb[sheet_name]
        headers: list[str] = []
        sample_rows: list[list[Any]] = []
        formulas_in_sheet: list[str] = []

        max_row = ws.max_row or 0
        max_col = ws.max_column or 0

        # Read first row as headers
        if max_row > 0:
            for col_idx in range(1, max_col + 1):
                val = ws.cell(row=1, column=col_idx).value
                headers.append(str(val) if val is not None else f"Col{col_idx}")

        # Sample rows (up to 5)
        for r_idx in range(2, min(max_row + 1, 7)):
            row_data = []
            for col_idx in range(1, max_col + 1):
                cell = ws.cell(row=r_idx, column=col_idx)
                c_val = cell.value
                if isinstance(c_val, str) and c_val.startswith("="):
                    formulas_in_sheet.append(c_val)
                    total_formulas += 1
                row_data.append(str(c_val) if c_val is not None else "")
            sample_rows.append(row_data)

        sheets_info.append({
            "name": sheet_name,
            "max_row": max_row,
            "max_col": max_col,
            "headers": headers,
            "sample_rows": sample_rows,
            "formula_count": len(formulas_in_sheet),
        })

    wb.close()

    return {
        "file_name": p.name,
        "sheet_count": len(sheets_info),
        "sheets": sheets_info,
        "total_formulas": total_formulas,
    }
