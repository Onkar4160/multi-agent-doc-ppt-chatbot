"""Edit regression guard (FR-EDIT-3, AC-6; ADR-033).

Compares the content units of two versions — in the model (by node ID) and in the rendered files (text per slide,
per section, per sheet) — and reports which units changed. Units outside the change scope must stay identical.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

_PURE_STYLE = re.compile(r"\b(add|remove|delete|rewrite|write|insert|expand|shorten|condense|replace|update|move|research)\b", re.I)


class RegressionReport(BaseModel):
    changed_units: list[str] = Field(default_factory=list)
    out_of_scope_changed: list[str] = Field(default_factory=list)
    file_units_compared: int = 0
    file_changed_units: list[str] = Field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.out_of_scope_changed


def is_pure_restyle(instruction: str, style_changes: list[Any]) -> bool:
    """Formatting only: style changes were parsed and no content verb is present."""
    return bool(style_changes) and not _PURE_STYLE.search(instruction)


def model_units(model: Any) -> dict[str, str]:
    """{unit id: canonical JSON} for slides, sections and sheets (style and IDs inside excluded)."""
    if hasattr(model, "slides"):
        return {s.node_id or f"slide_{i}": s.model_dump_json(exclude={"node_id"}) for i, s in enumerate(model.slides, 1)}
    if hasattr(model, "sections"):
        return {s.node_id or f"section_{i}": s.model_dump_json(exclude={"node_id"}) for i, s in enumerate(model.sections, 1)}
    if hasattr(model, "sheets"):
        return {s.node_id or f"sheet_{i}": s.model_dump_json(exclude={"node_id"}) for i, s in enumerate(model.sheets, 1)}
    return {}


def file_units(path: str | Path, kind: str) -> list[str]:
    """Text of each unit in a saved file: slide texts, section texts (split at Heading 1), sheet cell values."""
    if kind == "pptx":
        import pptx

        return [" ".join(sh.text_frame.text for sh in s.shapes if sh.has_text_frame) for s in pptx.Presentation(str(path)).slides]
    if kind == "docx":
        import docx

        units, cur = [], []
        for p in docx.Document(str(path)).paragraphs:
            if p.style.name == "Heading 1" and cur:
                units.append("\n".join(cur))
                cur = []
            cur.append(p.text)
        return units + ["\n".join(cur)]
    import openpyxl

    wb = openpyxl.load_workbook(str(path))
    return ["|".join(str(c.value) for row in ws.iter_rows() for c in row if c.value is not None) for ws in wb.worksheets]


def regression(old_model: Any, new_model: Any, targets: list[str] | None, *, old_file: str | Path | None = None,
               new_file: str | Path | None = None, kind: str = "") -> RegressionReport:
    old, new = model_units(old_model), model_units(new_model)
    changed = [k for k in new if old.get(k) != new[k]] + [k for k in old if k not in new]
    scope = set(targets or [])
    report = RegressionReport(changed_units=changed,
                              out_of_scope_changed=[k for k in changed if scope and k not in scope])
    if old_file and new_file and Path(old_file).exists() and Path(new_file).exists() and kind:
        try:
            a, b = file_units(old_file, kind), file_units(new_file, kind)
        except Exception:  # a file that does not open is reported by the file checks, not here
            a, b = [], [None]
        if len(a) == len(b):
            report.file_units_compared = len(a)
            report.file_changed_units = [f"{kind} unit {i}" for i, (x, y) in enumerate(zip(a, b), 1) if x != y]
    return report


def _units(model: Any) -> list[Any] | None:
    for attr in ("slides", "sections", "sheets"):
        if hasattr(model, attr):
            return getattr(model, attr)
    return None


def restore_out_of_scope(old_model: Any, new_model: Any, targets: list[str] | None) -> list[str]:
    """Undo changes the edit made to units it was not asked to touch; returns the restored unit IDs.

    Targets may name units ('slide_002') or elements inside them ('slide_002_bullet_001'); a whole-artifact target
    (or none) disables the guard. Added and removed units are left as the edit made them.
    """
    if not targets:
        return []
    old_units, new_units = _units(old_model), _units(new_model)
    if old_units is None or new_units is None:
        return []
    if any(t.startswith(("document_", "presentation_", "workbook_")) for t in targets):
        return []
    old_by_id = {u.node_id: u for u in old_units if u.node_id}
    restored = []
    for i, unit in enumerate(new_units):
        uid = unit.node_id
        if not uid or uid not in old_by_id or any(t == uid or t.startswith(uid + "_") for t in targets):
            continue
        before = old_by_id[uid]
        if unit.model_dump_json(exclude={"node_id"}) != before.model_dump_json(exclude={"node_id"}):
            new_units[i] = before.model_copy(deep=True)
            restored.append(uid)
    return restored
