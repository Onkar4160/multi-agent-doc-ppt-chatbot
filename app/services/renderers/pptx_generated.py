"""Generated design system for decks without a template (FR-PPTX-3, ADR-029).

A 16:9 deck drawn from a small token set: dark cover and section slides, light content slides with an accent rule
under an assertion headline, a consistent content region for bullets and native components, slide numbers and a
Sources slide. Everything is editable text, shapes, charts and tables.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pptx import Presentation
from pptx.util import Inches

from app.models.deck_model import DeckModel, SlideModel
from app.services.layout.text import Para
from app.services.renderers.pptx_archetypes import Palette, draw_blocks, has_visual, with_overrides
from app.services.renderers.pptx_canvas import BulletPara, Canvas, CanvasLog, Rect, TextStyle

W, H = 13.333, 7.5
MARGIN = 0.7
DEFAULT = Palette(accent="1F4E79", accent2="2E86AB", text="1F2933", muted="5B6770", surface="EEF2F6",
                  background="FFFFFF", on_accent="FFFFFF", heading_font="Calibri", body_font="Calibri")
DARK = "13293D"

# Presets by tone and audience (FR-PPTX-3): (palette, cover/section background)
PRESETS = {
    "formal": (DEFAULT, "13293D"),
    "friendly": (Palette(accent="E07A2E", accent2="2A9D8F", text="263238", muted="5F6B73", surface="FBF1E8",
                         on_accent="FFFFFF", heading_font="Segoe UI Semibold", body_font="Segoe UI"), "264653"),
    "technical": (Palette(accent="2F7D5B", accent2="3B6EA8", text="1E272E", muted="57606A", surface="EDF3F0",
                          on_accent="FFFFFF", heading_font="Calibri", body_font="Calibri"), "1B2A24"),
    "bold": (Palette(accent="C2185B", accent2="5E35B1", text="212121", muted="616161", surface="F6EEF2",
                     on_accent="FFFFFF", heading_font="Arial Black", body_font="Arial"), "1A1023"),
}
KEYWORDS = [("friendly", r"friendly|casual|warm|community|residents|students|kids|school|public"),
            ("technical", r"technical|engineer|developer|scientific|research|data|analytics"),
            ("bold", r"pitch|investor|launch|marketing|bold|startup")]


def preset_for(hint: str) -> tuple[Palette, str]:
    """Design preset from the request's tone, audience and purpose; formal by default."""
    import re

    for name, pattern in KEYWORDS:
        if re.search(r"\b(" + pattern + ")", hint or "", re.I):
            return PRESETS[name]
    return PRESETS["formal"]
HEAD = Rect(MARGIN, 0.45, W - 2 * MARGIN, 1.05)
BODY = Rect(MARGIN, 1.85, W - 2 * MARGIN, H - 1.85 - 0.75)


def _p(text: str, size: float, pal: Palette, *, bold: bool = False, heading: bool = False) -> Para:
    return Para(text=text, size_pt=size, bold=bold, font=pal.heading_font if heading else pal.body_font, space_after_pt=6)


def _cite(ids: list[int]) -> str:
    return " " + "".join(f"[{i}]" for i in sorted(set(ids))) if ids else ""


def render_generated(deck: DeckModel, out_path: str | Path, *, design_hint: str = "",
                     source_entries: list[str] | None = None) -> tuple[Path, CanvasLog, set[int]]:
    from app.services.pptx_renderer import strip_markdown

    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(W), Inches(H)
    blank = prs.slide_layouts[6]
    palette, dark = preset_for(design_hint)
    palette = with_overrides(palette, deck.style)
    log = CanvasLog()
    cited: set[int] = set()
    for n, s in enumerate(deck.slides, start=1):
        slide = prs.slides.add_slide(blank)
        c = Canvas(slide, log)
        title = strip_markdown(s.title)
        if s.role == "title":
            _cover(c, title, strip_markdown(s.subtitle or deck.subtitle or ""), palette, dark)
        elif s.role in ("section_header", "title_only") and not has_visual(s.blocks):
            _statement(c, title, strip_markdown(s.subtitle or s.key_message or ""), palette,
                       dark=s.role == "section_header")
        else:
            _content(c, s, title, palette, cited)
            _footer(c, n, palette)
        if s.notes:
            c.notes(strip_markdown(s.notes))
    if source_entries:
        slide = prs.slides.add_slide(blank)
        c = Canvas(slide, log)
        _headline(c, "Sources & References", palette)
        c.text(BODY, [BulletPara(_p(e, 12, palette), level=0) for e in source_entries], TextStyle(palette.text, bullet_color=palette.accent),
               name="content:sources", min_pt=9)
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    prs.save(out)
    return out, log, cited


def _cover(c: Canvas, title: str, subtitle: str, pal: Palette, dark: str = DARK) -> None:
    c.background(dark)
    c.rect(Rect(0, H - 0.35, W, 0.35), pal.accent, "decor:cover-band")
    c.rect(Rect(MARGIN, 3.55, 1.6, 0.08), pal.accent2, "decor:cover-rule")
    c.text(Rect(MARGIN, 1.4, W - 2 * MARGIN, 2.05), [BulletPara(_p(title, 44, pal, bold=True, heading=True))],
           TextStyle("FFFFFF", anchor="bottom"), name="content:title", min_pt=28)
    if subtitle:
        c.text(Rect(MARGIN, 3.8, W - 2 * MARGIN, 1.2), [BulletPara(_p(subtitle, 20, pal))], TextStyle("C9D6E3"),
               name="content:subtitle", min_pt=14)


def _statement(c: Canvas, title: str, sub: str, pal: Palette, *, dark: bool) -> None:
    if dark:
        c.background(pal.accent)
    c.text(Rect(MARGIN * 1.5, 2.2, W - 3 * MARGIN, 1.8), [BulletPara(_p(title, 38, pal, bold=True, heading=True))],
           TextStyle("FFFFFF" if dark else pal.text, align="center", anchor="bottom"), name="content:title", min_pt=24)
    if sub:
        c.text(Rect(MARGIN * 1.5, 4.2, W - 3 * MARGIN, 1.2), [BulletPara(_p(sub, 20, pal))],
               TextStyle("E6EEF5" if dark else pal.muted, align="center"), name="content:subtitle", min_pt=14)


def _headline(c: Canvas, title: str, pal: Palette) -> None:
    c.text(HEAD, [BulletPara(_p(title, 30, pal, bold=True, heading=True))], TextStyle(pal.text, anchor="bottom"),
           name="content:title", min_pt=20)
    c.rect(Rect(MARGIN, HEAD.bottom_y + 0.08, 1.2, 0.07), pal.accent, "decor:title-rule")


def _footer(c: Canvas, n: int, pal: Palette) -> None:
    c.text(Rect(W - MARGIN - 1.0, H - 0.55, 1.0, 0.35), [BulletPara(_p(str(n), 11, pal))], TextStyle(pal.muted, align="right"),
           name="decor:slide-number", min_pt=9)


def _content(c: Canvas, s: SlideModel, title: str, pal: Palette, cited: set[int]) -> None:
    _headline(c, title, pal)
    for b in s.blocks:
        cited.update(getattr(b, "source_ids", []) or [])
    if has_visual(s.blocks):
        draw_blocks(c, BODY, s.blocks, pal)
        return
    if s.left and s.right:
        for i, (items, r) in enumerate(zip((s.left, s.right), BODY.split_h(2, 0.4))):
            c.rect(r, pal.surface, f"decor:card-{i + 1}", rounded=True)
            paras = []
            for b in items:
                cited.update(b.source_ids)
                paras.append(BulletPara(_p(b.text + _cite(b.source_ids), 18 if b.level else 20, pal, bold=not b.level), level=b.level or None))
            c.text(r.inset(0.3, 0.25), paras, TextStyle(pal.text, bullet_color=pal.accent), name=f"content:column-{i + 1}", min_pt=12)
        return
    items = s.bullets or s.left or s.right
    paras = []
    for b in items:
        cited.update(b.source_ids)
        paras.append(BulletPara(_p(b.text + _cite(b.source_ids), 20 if b.level == 0 else 17, pal), level=b.level))
    if s.subtitle and not paras:
        paras = [BulletPara(_p(s.subtitle, 22, pal))]
    if paras:
        c.text(BODY, paras, TextStyle(pal.text, bullet_color=pal.accent), name="content:body", min_pt=12)
