"""Excel renderer: WorkbookModel → styled, working .xlsx (FR-XLSX-1..5, FR-XLSX-7; ADR-031).

Tables are placed exactly at their declared range (formulas elsewhere rely on it) as real Excel tables with
filters, typed number formats and units in headers; header rows are frozen; assumptions become defined names;
charts reference real ranges with category labels; data columns get data bars and assumptions get validation.
"""

from __future__ import annotations

import logging
import re
import uuid
from pathlib import Path
from typing import Any

import openpyxl
from openpyxl.chart import AreaChart, BarChart, LineChart, PieChart, Reference, ScatterChart
from openpyxl.formatting.rule import DataBarRule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter, range_boundaries
from openpyxl.workbook.defined_name import DefinedName
from openpyxl.worksheet.datavalidation import DataValidation
from openpyxl.worksheet.table import Table, TableStyleInfo

from app.core.config import get_settings
from app.models.workbook_model import WorkbookModel

logger = logging.getLogger(__name__)

ACCENT = "1F497D"
THIN = Side(border_style="thin", color="D9D9D9")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
TAB_COLORS = {"readme": "7F7F7F", "assumptions": "E07A2E", "data": "2E86AB", "calculations": "2F7D5B",
              "report": "1F497D", "dashboard": "1F497D", "sources": "7F7F7F"}


def _number_format(header: str, values: list[Any]) -> str | None:
    h = header.lower()
    nums = [v for v in values if isinstance(v, (int, float)) and not isinstance(v, bool)]
    if not nums:
        return None
    if "%" in h or "rate" in h or "share" in h or "percent" in h:
        return "0.0%" if all(abs(v) <= 1.5 for v in nums) else '0.0"%"'
    if "$" in h or "usd" in h or "price" in h or "cost" in h or "revenue" in h or "amount" in h:
        return '"$"#,##0.00' if any(isinstance(v, float) and v != int(v) for v in nums) else '"$"#,##0'
    if "year" in h or "id" == h.strip() or h.endswith(" id"):
        return "0"
    if all(isinstance(v, int) or float(v).is_integer() for v in nums):
        return "#,##0"
    return "#,##0.##"


def _table_name(name: str, used: set[str]) -> str:
    base = re.sub(r"[^A-Za-z0-9_]", "_", name or "Table") or "Table"
    if not re.match(r"[A-Za-z_]", base):
        base = "T_" + base
    out, n = base[:200], 2
    while out.lower() in used:
        out = f"{base}_{n}"
        n += 1
    used.add(out.lower())
    return out


def render_xlsx(model: WorkbookModel, output_path: str | Path | None = None) -> Path:
    """Render a WorkbookModel into a working Excel workbook and return its path."""
    if output_path is None:
        out_dir = Path(get_settings().storage_dir) / "outputs"
        out_dir.mkdir(parents=True, exist_ok=True)
        dest = out_dir / f"workbook_{uuid.uuid4().hex[:8]}.xlsx"
    else:
        dest = Path(output_path)
        dest.parent.mkdir(parents=True, exist_ok=True)
    accent = (model.style.accent_color if model.style and model.style.accent_color else ACCENT)
    font_name = (model.style.body_font if model.style and model.style.body_font else "Calibri")
    header_fill = PatternFill(start_color=accent, end_color=accent, fill_type="solid")
    header_font = Font(name=font_name, size=11, bold=True, color="FFFFFF")

    wb = openpyxl.Workbook()
    if model.sheets:
        wb.remove(wb.active)
    used_tables: set[str] = set()
    for idx, sm in enumerate(model.sheets, start=1):
        ws = wb.create_sheet(title=(sm.name or f"Sheet{idx}")[:31])
        if sm.role in TAB_COLORS:
            ws.sheet_properties.tabColor = TAB_COLORS[sm.role]
        next_row = 1
        freeze = None
        for t in sm.tables:
            try:
                min_col, min_row, _, _ = range_boundaries(t.range.replace("$", "").split("!")[-1])
            except Exception:
                min_col, min_row = 1, next_row
            if min_row < next_row:  # overlapping declared ranges: stack below instead
                min_row = next_row
            ncols = max(len(t.headers), max((len(r) for r in t.rows), default=0), 1)
            r = min_row
            if t.headers:
                for j, h in enumerate(t.headers):
                    c = ws.cell(row=r, column=min_col + j, value=str(h))
                    c.fill, c.font, c.border = header_fill, header_font, BORDER
                    c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
                r += 1
            formats = [_number_format(str(t.headers[j]) if j < len(t.headers) else "", [row[j] for row in t.rows if j < len(row)])
                       for j in range(ncols)]
            for row in t.rows:
                for j, v in enumerate(row):
                    c = ws.cell(row=r, column=min_col + j, value=v)
                    c.border = BORDER
                    c.font = Font(name=font_name, size=11)
                    if formats[j] and (isinstance(v, (int, float)) or (isinstance(v, str) and v.startswith("="))):
                        c.number_format = formats[j]
                r += 1
            last_row = max(r - 1, min_row + 1)
            if t.headers and t.rows and len(set(map(str, t.headers))) == len(t.headers):
                ref = f"{get_column_letter(min_col)}{min_row}:{get_column_letter(min_col + ncols - 1)}{last_row}"
                tab = Table(displayName=_table_name(t.name, used_tables), ref=ref)
                tab.tableStyleInfo = TableStyleInfo(name="TableStyleMedium2", showRowStripes=True)
                ws.add_table(tab)
                if sm.role == "data":  # FR-XLSX-5: magnitude at a glance
                    for j, f in enumerate(formats):
                        if f and t.rows:
                            col = get_column_letter(min_col + j)
                            ws.conditional_formatting.add(f"{col}{min_row + 1}:{col}{last_row}",
                                                          DataBarRule(start_type="min", end_type="max", color=accent))
                            break
                if sm.role == "assumptions" and ncols >= 2:
                    dv = DataValidation(type="decimal", operator="greaterThanOrEqual", formula1="-1000000000",
                                        showErrorMessage=True, errorTitle="Number required", error="Enter a number.")
                    ws.add_data_validation(dv)
                    dv.add(f"{get_column_letter(min_col + 1)}{min_row + 1}:{get_column_letter(min_col + 1)}{last_row}")
            if freeze is None and t.headers and min_row == 1:
                freeze = f"A{min_row + 1}"
            next_row = last_row + 2
        for cm in sm.cells:
            c = ws[cm.coordinate]
            c.value = cm.formula if cm.formula else cm.value
            if cm.format:
                c.number_format = cm.format
            c.font = Font(name=font_name, size=11, bold=(cm.coordinate.startswith("A") and sm.role != "readme" and c.row == 1))
            if sm.role == "readme" and c.row == 1:
                c.font = Font(name=font_name, size=16, bold=True, color=accent)
        if freeze and sm.role in ("data", "assumptions", "calculations", "sources"):
            ws.freeze_panes = freeze
        anchor_row = max(ws.max_row + 2, 3)
        for ch in sm.charts:
            try:
                _add_chart(wb, ws, ch, f"{get_column_letter(min(ws.max_column + 2, 8))}2" if ws.max_row < 30 else f"A{anchor_row}")
                anchor_row += 18
            except Exception as exc:
                logger.warning("Chart '%s' skipped: %s", ch.title, exc)
        for col in ws.columns:
            width = max((len(str(c.value)) for c in col if c.value is not None and not str(c.value).startswith("=")), default=8)
            ws.column_dimensions[get_column_letter(col[0].column)].width = min(max(width + 3, 10), 60)
    for nr in model.named_ranges:
        try:
            wb.defined_names[nr.name] = DefinedName(nr.name, attr_text=_absolute(nr.ref))
        except Exception as exc:
            logger.warning("Defined name %s skipped: %s", nr.name, exc)
    wb.save(str(dest))
    logger.info("Rendered XLSX workbook to %s", dest)
    return dest


def _absolute(ref: str) -> str:
    sheet, _, cells = ref.rpartition("!")
    cells = re.sub(r"\$?([A-Z]{1,3})\$?(\d+)", r"$\1$\2", cells)
    sheet = sheet.strip("'")
    return f"'{sheet}'!{cells}" if sheet and " " in sheet else (f"{sheet}!{cells}" if sheet else cells)


def _ref(wb: Any, default_ws: Any, rng: str) -> tuple[Any, int, int, int, int]:
    sheet, _, cells = rng.rpartition("!")
    ws = wb[sheet.strip("'")] if sheet else default_ws
    min_col, min_row, max_col, max_row = range_boundaries(cells.replace("$", ""))
    return ws, min_col, min_row, max_col, max_row


def _add_chart(wb: Any, ws: Any, ch: Any, anchor: str) -> None:
    kinds = {"bar": BarChart, "column": BarChart, "line": LineChart, "pie": PieChart, "area": AreaChart, "scatter": ScatterChart}
    chart = kinds.get(ch.chart_type, BarChart)()
    if isinstance(chart, BarChart):
        chart.type = "bar" if ch.chart_type == "bar" else "col"
    chart.title = ch.title or None
    dws, c1, r1, c2, r2 = _ref(wb, ws, ch.data_range)
    header = isinstance(dws.cell(r1, c1).value, str)  # a text first cell is the series name
    chart.add_data(Reference(dws, min_col=c1, min_row=r1, max_col=c2, max_row=r2), titles_from_data=header)
    if ch.categories_range:
        cws, k1, q1, k2, q2 = _ref(wb, ws, ch.categories_range)
        chart.set_categories(Reference(cws, min_col=k1, min_row=q1, max_col=k2, max_row=q2))
    chart.height, chart.width = 8, 16
    ws.add_chart(chart, anchor)
