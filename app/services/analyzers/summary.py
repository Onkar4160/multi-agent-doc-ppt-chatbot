"""Stable, compact view of a template profile: for the CLI, the UI and manifest snapshot tests.

Only structural facts appear here (no LLM-derived tone), so the same template always gives the same summary.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from app.models.template_profile import TemplateProfile


class NoLLM:
    """Skips the tone call of the analysers (offline structural analysis)."""

    _mock_mode = False

    def generate_json(self, *args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("tone analysis skipped")


def manifest_summary(profile: TemplateProfile) -> dict[str, Any]:
    out: dict[str, Any] = {"file": profile.source_name, "type": profile.file_type}
    t = profile.design_tokens
    if t is not None:
        out["tokens"] = {"heading_font": t.heading_font, "body_font": t.body_font, "primary": t.primary, "source": t.source}
    ps = profile.ppt_style
    if ps is not None:
        roles = Counter(e.role for s in ps.inventory for e in s.elements)
        design = ps.slide_design
        out.update({
            "slide_size_in": [ps.slide_width, ps.slide_height],
            "sample_slides": len(ps.inventory),
            "design_mode": design.design_mode if design else None,
            "bases": [b.kind for b in design.slide_bases] if design else [],
            "layouts": {lay.name: lay.archetype for lay in ps.layouts},
            "roles": dict(sorted(roles.items())),
            "branding": [f"{e.kind}: {e.reason}" for s in ps.inventory for e in s.elements if e.role == "branding"]
            + [f"{b.kind}: {b.reason}" for b in ps.branding],
        })
    ds = profile.doc_style
    if ds is not None and ds.manifest is not None:
        m = ds.manifest
        out.update({
            "page_size": ds.page_size,
            "sections": [{"orientation": s.orientation, "margins_in": s.margins_in, "header": bool(s.header_text),
                          "footer": bool(s.footer_text), "page_numbers": s.has_page_number} for s in m.sections],
            "styles": {role: f"{st.style_name}: {st.font} {st.size_pt}" for role, st in sorted(m.styles_by_role.items())},
            "cover_lines": len(m.cover_lines),
            "tables": len(m.tables),
            "numbering_definitions": m.numbering_definitions,
            "has_toc": m.has_toc,
        })
    xs = profile.xlsx_style
    if xs is not None:
        out.update({
            "sheets": [{"name": s.name, "role": s.role, "tables": [t["name"] for t in s.tables], "formulas": s.formula_count,
                        "reads": s.references_sheets, "charts": [c["type"] for c in s.charts],
                        "conditional_formats": s.conditional_formats, "validations": s.data_validations} for s in xs.sheets],
            "defined_names": xs.defined_names,
        })
    return out
