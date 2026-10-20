"""Clone-based rendering for templates whose design lives on the sample slides (Canva-style templates).

Ported from DocForge ``render/pptx/clone.py`` (ADR-019) and the template parts of its composer.
Each output slide starts as a copy of the best sample slide for its role: backgrounds, artwork, panels and
logos are kept; sample text, charts and tables are dropped; the title box keeps its styling for our
headline; the area the sample body text used becomes the content region. Sample slides are removed at
the end, so only cloned slides remain.
"""

from __future__ import annotations

import logging
import re
from copy import deepcopy
from dataclasses import dataclass, field, replace
from typing import Any

from app.models.deck_model import BulletItem, DeckModel, SlideModel
from app.models.template_profile import Box, SlideBase, SlideDesign
from app.services.layout.backdrop import color_under, is_dark
from app.services.layout.colors import SlideTokens, contrast_ratio, relative_luminance, tokens_from_template
from app.services.renderers.pptx_archetypes import Palette, draw_blocks, has_visual
from app.services.renderers.pptx_media import _slot_frame, clear_of_artwork, round_corners, split_for_photo, swap_photo
from app.services.layout.text import EMU_PER_INCH, Para
from app.services.renderers.pptx_canvas import (
    BulletPara,
    Canvas,
    CanvasLog,
    Rect,
    TextStyle,
    content_height,
    rgb,
    set_geometry,
)

logger = logging.getLogger(__name__)

MIN_BODY, MIN_CAPTION, MIN_HEADLINE, MIN_TITLE = 14, 10, 22, 24
GAP = 0.35
CARD_PAD_X, CARD_PAD_Y = 0.3, 0.28
MAX_BULLETS, MAX_BULLET_WORDS = 6, 22

_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_REL_ATTRS = (f"{{{_R}}}embed", f"{{{_R}}}link", f"{{{_R}}}id", f"{{{_R}}}pict")
_CLOSING = re.compile(r"\b(thank|questions|q\s*&\s*a|contact us|the end|get in touch)\b", re.IGNORECASE)
_STRUCTURAL = {"cover", "section", "closing"}


def _in(v: int) -> float:
    return v / EMU_PER_INCH


def slide_kind(slide: SlideModel, position: int) -> str:
    """Archetype of a deck slide: cover | section | closing | two | dense | content | references."""
    title = slide.title or ""
    if slide.archetype == "closing" and position > 0:
        return "closing"
    if has_visual(slide.blocks):
        return "visual"  # charts, tables, timelines... need the widest content area
    if slide.role == "title":
        return "closing" if position > 0 and _CLOSING.search(title) else "cover"
    if slide.role in ("section_header", "title_only") and _CLOSING.search(title):
        return "closing"
    if slide.role == "section_header":
        return "section"
    if slide.role == "two_content":
        return "two"
    # Long lists need a medium-sized content area, like DocForge's two-column and agenda archetypes.
    return "dense" if len(slide.bullets) > 4 or (slide.subtitle and len(slide.bullets) > 3) else "content"


def assign_bases(kinds: list[str], design: SlideDesign) -> list[SlideBase]:
    """Deterministic base per slide: cover/closing by role, content bases rotated for variety."""
    full = design.slide_width * design.slide_height
    bases = design.slide_bases
    covers = [b for b in bases if b.kind == "cover"]
    closings = [b for b in bases if b.kind == "closing"]
    sections = [b for b in bases if b.kind == "section"]
    contents = sorted((b for b in bases if b.kind == "content"), key=lambda b: -b.score)
    light = [b for b in contents if not is_dark(b.region_color)]
    contents = light or contents  # our body text is dark; dark panels are used only if nothing else exists
    contents = [b for b in contents if not b.title_blocked] or contents  # headlines must not run into artwork
    sw = design.slide_width

    def roomy(b: SlideBase, area: float, width: float) -> bool:
        return b.region is not None and b.region.area() / full >= area and b.region.w / sw >= width

    contents = [b for b in contents if roomy(b, 0.1, 0.3)] or contents
    large = [b for b in contents if roomy(b, 0.18, 0.45)] or contents
    wide = [b for b in large if roomy(b, 0.2, 0.55)] or large
    fallback = (contents or covers or bases)[0]
    counters = {"light": 0, "heavy": 0, "wide": 0, "section": 0}
    out: list[SlideBase] = []

    def rotate(pool: list[SlideBase], key: str) -> SlideBase:
        pick = pool[counters[key] % len(pool)]
        if out and pick.index == out[-1].index and len(pool) > 1:  # never the same design twice in a row
            counters[key] += 1
            pick = pool[counters[key] % len(pool)]
        counters[key] += 1
        return pick

    for kind in kinds:
        if kind == "cover":
            out.append(covers[0] if covers else fallback)
        elif kind == "closing":
            out.append((closings or covers or [fallback])[0])
        elif kind == "section":
            out.append(rotate(sections, "section") if sections else (covers or [fallback])[0])
        elif kind in ("references", "visual"):
            out.append(rotate(wide, "wide") if wide else fallback)
        elif kind in ("two", "dense"):
            out.append(rotate(large, "heavy") if large else fallback)
        else:
            out.append(rotate(contents, "light") if contents else fallback)
    return out


def _copy_shape(src_part, dst_part, element):
    el = deepcopy(element)
    for node in el.iter():
        for attr in _REL_ATTRS:
            rid = node.get(attr)
            if not rid or rid not in src_part.rels:
                continue
            rel = src_part.rels[rid]
            if rel.is_external:
                new = dst_part.relate_to(rel.target_ref, rel.reltype, is_external=True)
            else:
                new = dst_part.relate_to(rel.target_part, rel.reltype)
            node.set(attr, new)
    return el


@dataclass(frozen=True)
class Frame:
    """Slide geometry in inches, derived per base."""

    w: float
    h: float
    m: float
    headline: Rect
    body: Rect
    footer_y: float
    scale: float = 1.0  # type scale relative to a 7.5-inch-tall slide


@dataclass
class CloneContext:
    design: SlideDesign
    sources: list  # original sample slides
    plan: list[SlideBase] = field(default_factory=list)
    _cursor: int = 0
    _sample_ids: list = field(default_factory=list)
    _title_geometry: dict = field(default_factory=dict)

    @classmethod
    def open(cls, prs, design: SlideDesign) -> CloneContext:
        return cls(design=design, sources=list(prs.slides), _sample_ids=list(prs.slides._sldIdLst))

    def prepare(self, kinds: list[str]) -> None:
        self.plan = assign_bases(kinds, self.design)
        self._cursor = 0

    def add_slide(self, prs, kind: str):
        if self._cursor < len(self.plan):
            base = self.plan[self._cursor]
        else:  # slides added after planning (the automatic sources slide)
            base = assign_bases([kind], self.design)[0]
        self._cursor += 1
        src = self.sources[base.index]
        slide = prs.slides.add_slide(src.slide_layout)
        tree = slide.shapes._spTree
        for shape in list(slide.shapes):  # layout placeholders: the clone brings its own shapes
            tree.remove(shape._element)
        bg = src._element.cSld.bg
        if bg is not None:
            slide._element.cSld.insert(0, _copy_shape(src.part, slide.part, bg))
        drop = set(base.remove_shapes)
        kept: list[int] = []
        for i, shape in enumerate(src.shapes):
            if i in drop:
                continue
            tree.append(_copy_shape(src.part, slide.part, shape._element))
            kept.append(i)
        by_index = dict(zip(kept, list(slide.shapes), strict=True))
        title_shape = by_index.get(base.title_shape) if base.title_shape is not None else None
        if title_shape is not None and base.kind == "content":
            # Cover, section and closing titles keep the designer's box: widening them would cross the colour
            # panels those slides are built from. Content headlines need the room of the content area.
            self._widen_title(title_shape, base)
        elif title_shape is not None:  # the body area of a structural slide starts below its real title
            self._title_geometry[base.index] = (int(title_shape.left), int(title_shape.top),
                                                int(title_shape.width), int(title_shape.height))
        slots = {
            "title": title_shape,
            "subtitle": by_index.get(base.subtitle_shape) if base.subtitle_shape is not None else None,
            "photos": [by_index[i] for i in base.photo_shapes if i in by_index],
            "frames": [by_index[i] for i in base.frame_shapes if i in by_index],
            "samples": [by_index[i] for i in getattr(base, "sample_photos", []) if i in by_index],
            "sample_title": base.title_text,
        }
        if slots["subtitle"] is not None:
            slots["subtitle_frame"] = _slot_frame(slide, slots["subtitle"], self.design)
            if title_shape is not None and slots["subtitle_frame"] is None:
                self._widen_like(slots["subtitle"], title_shape)
        return slide, self._frame(base), slots

    def _widen_title(self, shape, base: SlideBase) -> None:
        sw, sh = self.design.slide_width, self.design.slide_height
        margin = int(sw * 0.04)
        x, y, w, h = int(shape.left), int(shape.top), int(shape.width), int(shape.height)
        # Grow within the title's own column and the content area below it, never across panels.
        region = base.region
        target_w = min(max(w, region.w if region else int(sw * 0.6)), sw - 2 * margin)
        algn = shape.text_frame.paragraphs[0].alignment if shape.has_text_frame else None
        name = getattr(algn, "name", "") or ""
        if name == "CENTER":
            nx = x + w // 2 - target_w // 2
        elif name == "RIGHT":
            nx = x + w - target_w
        else:
            nx = x
        lo = min(x, region.x) if region else margin
        hi = max(x + w, region.right) if region else sw - margin
        nx = min(max(nx, lo, margin), max(hi - target_w, margin))
        nh = h
        if base.kind == "content" and base.region is not None and base.region.y > y + h:
            nh = max(h, min(base.region.y - y - int(sh * 0.02), int(h * 1.8)))
        shape.left, shape.top, shape.width, shape.height = nx, y, target_w, nh
        self._title_geometry[base.index] = (nx, y, target_w, nh)

    def _widen_like(self, shape, title) -> None:
        """Subtitles get the title's width (sample subtitles are sized to one short line)."""
        sw = self.design.slide_width
        margin = int(sw * 0.04)
        if int(shape.width) < int(title.width):
            width = min(int(title.width), sw - 2 * margin)
            center = int(shape.left) + int(shape.width) // 2
            shape.width = width
            shape.left = min(max(margin, center - width // 2), sw - margin - width)

    def _frame(self, base: SlideBase) -> Frame:
        w, h = _in(self.design.slide_width), _in(self.design.slide_height)
        scale = h / 7.5
        geo = self._title_geometry.get(base.index)
        if geo is not None:
            headline = Rect(*(_in(v) for v in geo))
        else:
            headline = Rect(0.6 * scale, 0.42 * scale, w - 1.2 * scale, 1.2 * scale)
        if base.region is not None:
            r: Box = base.region
            body = Rect(_in(r.x), _in(r.y), _in(r.w), _in(r.h))
        else:
            top = headline.y + headline.h + 0.3 * scale
            body = Rect(headline.x, top, headline.w, max(1.0, h - top - 0.8 * scale))
        margin = max(0.3 * scale, min(headline.x, body.x))
        return Frame(w=w, h=h, m=margin, headline=headline, body=body, footer_y=h - 0.45 * scale, scale=scale)

    def finish_slide(self, slide, slots: dict | None = None) -> None:
        """Drop placeholders that stayed empty (unfilled photo frames would show prompts in edit mode)."""
        for ph in list(slide.placeholders):
            empty = not ph.has_text_frame or not ph.text_frame.text.strip()
            if empty:
                ph._element.getparent().remove(ph._element)
        slots = slots or {}
        for key in ("title", "subtitle"):
            shape = slots.get(key)
            # A slot our content did not use still holds the template's sample words.
            if shape is not None and not shape.name.startswith("content:"):
                shape._element.getparent().remove(shape._element)

    def close(self, prs) -> None:
        id_list = prs.slides._sldIdLst
        for sld_id in self._sample_ids:
            prs.part.drop_rel(sld_id.rId)
            id_list.remove(sld_id)


# ── Composition of this project's DeckModel on cloned bases ────────────────


def _bullet_text(item: BulletItem) -> str:
    from app.services.copyfit import smart_trim, strip_citations
    from app.services.pptx_renderer import strip_markdown

    text = smart_trim(strip_citations(strip_markdown(item.text)), MAX_BULLET_WORDS)
    if item.source_ids:
        text += " " + "".join(f"[{sid}]" for sid in sorted(set(item.source_ids)))
    return text


def _lines(items: list[BulletItem]) -> list[tuple[str, int | None]]:
    return [(_bullet_text(b), min(1, max(0, b.level))) for b in items[:MAX_BULLETS] if b.text.strip()]


class CloneComposer:
    def __init__(self, context: CloneContext, tokens: SlideTokens) -> None:
        self.tpl = context
        self.t = tokens
        self.f: Frame | None = None
        self.slots: dict = {}

    def compose(
        self,
        prs,
        deck: DeckModel,
        slides: list[SlideModel],
        source_entries: list[str],
        photos: list[Any] | None = None,
    ) -> CanvasLog:
        from app.services.pptx_renderer import strip_markdown

        log = CanvasLog()
        photos = photos or [None] * len(slides)
        credits = list(dict.fromkeys(f"{p.credit} - {p.landing_url}" for p in photos if p is not None))
        source_entries = source_entries + [f"Image: {c}" for c in credits]
        kinds = [slide_kind(s, i) for i, s in enumerate(slides)]
        self.tpl.prepare(kinds + (["references"] if source_entries else []))
        for slide_model, kind, photo in zip(slides, kinds, photos, strict=True):
            slide, self.f, self.slots = self.tpl.add_slide(prs, kind)
            canvas = Canvas(slide, log, scale=self.f.scale)
            slotted = any(self.slots.get(k) for k in ("photos", "samples", "frames"))
            side = photo if (photo is not None and not slotted and kind not in _STRUCTURAL
                             and not has_visual(slide_model.blocks)) else None
            if kind in _STRUCTURAL:
                self._structural(canvas, slide_model, deck, kind)
            elif has_visual(slide_model.blocks):
                self._visual(canvas, slide_model)
            elif kind == "two" and side is None:
                self._two_content(canvas, slide_model)
            else:
                side = side if self._bullets(canvas, slide_model, side_photo=side) else None
            filled = side is not None or (photo is not None and slotted and self._fill_photo(canvas, photo))
            self.tpl.finish_slide(slide, self.slots)
            notes = strip_markdown(slide_model.notes or "")
            if filled:
                notes = f"{notes}\n\n{photo.credit} ({photo.landing_url})".strip()
            if notes:
                canvas.notes(notes)
        if source_entries:
            slide, self.f, self.slots = self.tpl.add_slide(prs, "references")
            self._references(Canvas(slide, log, scale=self.f.scale), source_entries)
            self.tpl.finish_slide(slide, self.slots)
        self.tpl.close(prs)
        return log

    # ---------------------------------------------------------------- text helpers

    def _p(self, text: str, size: float, *, bold: bool = False, heading: bool = False, after: float = 0) -> Para:
        return Para(
            text,
            round(size * self.f.scale, 1),
            bold=bold,
            font=self.t.heading_font if heading else self.t.body_font,
            space_after_pt=after,
        )

    def _bullet_paras(self, lines: list[tuple[str, int | None]], *, base: float | None = None) -> list[BulletPara]:
        # One body size for the whole deck (slides that differ only in point count must not change type size);
        # the fitter shrinks a slide only when its text would not fit.
        size = base or (20 if len(lines) <= 5 else 18)
        return [BulletPara(self._p(text, size if not level else size - 2, after=12), level=level) for text, level in lines]

    def _body_style(self, **kw) -> TextStyle:
        return TextStyle(self.t.text, bullet_color=self.t.primary, **kw)

    # ---------------------------------------------------------------- slide kinds

    def _structural(self, c: Canvas, s: SlideModel, deck: DeckModel, kind: str) -> None:
        """Cover, section and closing slides use the template's own text boxes (fidelity first)."""
        from app.services.pptx_renderer import strip_markdown

        title = " ".join(strip_markdown(s.title or deck.title).split())
        subtitle = strip_markdown(s.subtitle or (deck.subtitle if kind == "cover" else "")).strip()
        second: list[tuple[str, int | None]] = [(subtitle, None)] if subtitle else []
        if kind == "closing":
            second += _lines(s.bullets)
        title_ph = self.slots.get("title")
        size = {"cover": 44, "section": 36, "closing": 36}[kind]
        if title_ph is not None:
            self._readable_span(c, title_ph)
            c.placeholder_text(
                title_ph, [BulletPara(self._p(title, size, heading=True))], name="content:title", min_pt=MIN_TITLE
            )
            c.tighten(title_ph)
            self._readable(c, title_ph)
            self._clear_title(c, title_ph)
        else:
            self._headline(c, title)
        if not second:
            return
        body_ph = self.slots.get("subtitle")
        if body_ph is not None:
            self._readable_span(c, body_ph)
        paras = [BulletPara(self._p(t, 20 if kind == "cover" else 18), level=lvl) for t, lvl in second]
        if body_ph is None:
            c.text(self.f.body, paras, self._body_style(), name="content:body", min_pt=MIN_BODY)
            return
        # The subtitle box holds one line; further points (closing actions) go below it at body size,
        # when the slide has room for them there.
        room = self.f.h - (int(body_ph.top) + int(body_ph.height)) / EMU_PER_INCH - 0.85 * self.f.scale
        if room < 0.45 * self.f.scale * max(len(paras) - 1, 1):
            c.placeholder_text(body_ph, paras, name="content:subtitle", min_pt=MIN_BODY)
            self._fit_frame(body_ph, self.slots.get("subtitle_frame"), title_ph)
            self._readable(c, body_ph)
            if title_ph is not None:
                c.separate(title_ph, body_ph, self.slots.get("subtitle_frame"))
            return
        c.placeholder_text(body_ph, paras[:1], name="content:subtitle", min_pt=MIN_BODY)
        self._fit_frame(body_ph, self.slots.get("subtitle_frame"), title_ph)
        self._readable(c, body_ph)
        if title_ph is not None:
            c.separate(title_ph, body_ph, self.slots.get("subtitle_frame"))
        rest = paras[1:]
        if rest:
            top = (int(body_ph.top) + int(body_ph.height)) / EMU_PER_INCH + 0.25 * self.f.scale
            area = Rect(
                int(body_ph.left) / EMU_PER_INCH,
                top,
                int(body_ph.width) / EMU_PER_INCH,
                max(self.f.h - top - 0.6 * self.f.scale, 0.5),
            )
            c.text(
                area,
                [BulletPara(bp.para, level=0) for bp in rest],
                self._body_style(align="center"),
                name="content:body",
                min_pt=MIN_BODY,
            )

    def _headline(self, c: Canvas, text: str) -> None:
        title_ph = self.slots.get("title")
        if title_ph is not None:
            c.placeholder_text(
                title_ph, [BulletPara(self._p(text, 30, heading=True))], name="content:headline", min_pt=MIN_HEADLINE
            )
            self._readable(c, title_ph)
            self._clear_title(c, title_ph)
            bottom = (int(title_ph.top) + int(title_ph.height)) / EMU_PER_INCH + 0.15 * self.f.scale
            body = self.f.body
            if body.y < bottom < body.y + body.h * 0.6:
                self.f = replace(self.f, body=Rect(body.x, bottom, body.w, body.y + body.h - bottom))
            return
        c.text(
            self.f.headline,
            [BulletPara(self._p(text, 30, bold=True, heading=True))],
            TextStyle(self.t.text),
            name="content:headline",
            min_pt=MIN_HEADLINE,
        )

    def _title_text(self, s: SlideModel) -> str:
        from app.services.pptx_renderer import strip_markdown

        return " ".join(strip_markdown(s.title).split())

    def _lead(self, c: Canvas, s: SlideModel) -> Rect:
        """Render the subtitle as a lead line above the body; returns the remaining body area."""
        from app.services.pptx_renderer import strip_markdown

        body = clear_of_artwork(c.slide, self.f.body, self.f.scale, self.tpl.design.slide_width * self.tpl.design.slide_height)
        self.f = replace(self.f, body=body)
        text = strip_markdown(s.subtitle or "").strip()
        if not text:
            return body
        lead, rest = body.take_top(0.55 * self.f.scale, 0.2 * self.f.scale)
        c.text(lead, [BulletPara(self._p(text, 18))], TextStyle(self.t.text_muted), name="content:lead", min_pt=MIN_BODY)
        return rest

    def _bullets(self, c: Canvas, s: SlideModel, side_photo: Any = None) -> bool:
        """Headline, lead and points; True when side_photo was placed beside the text."""
        self._headline(c, self._title_text(s))
        body = self._lead(c, s)
        lines = _lines(s.bullets or s.left + s.right)
        if side_photo is not None and body.w >= 0.5 * self.f.w:  # a narrow panel keeps its text readable instead
            text_r, photo_r = split_for_photo(body, self.f.scale)
            c.picture(photo_r, side_photo.path, name="content:image", alt=side_photo.alt_text)
            round_corners(c.slide.shapes[-1])
            body = text_r
        else:
            side_photo = None
        if lines:
            c.text(body, self._bullet_paras(lines), self._body_style(), name="content:body", min_pt=MIN_BODY)
        return side_photo is not None

    def _visual(self, c: Canvas, s: SlideModel) -> None:
        """Native chart, table, KPI, timeline, process, comparison or quote in the template's content region."""
        self._headline(c, self._title_text(s))
        self.f = replace(self.f, body=clear_of_artwork(c.slide, self.f.body, self.f.scale,
                                                       self.tpl.design.slide_width * self.tpl.design.slide_height))
        t = self.t
        palette = Palette(accent=t.primary, accent2=t.secondary, text=t.text, muted=t.text_muted, surface=t.surface,
                          background=t.background, on_accent="FFFFFF" if relative_luminance(t.primary) < 0.4 else "1F2933",
                          heading_font=t.heading_font, body_font=t.body_font)
        body = self.f.body
        floor = 7.5 * self.f.scale * 0.88  # visuals may use the free space under the sample text area
        draw_blocks(c, Rect(body.x, body.y, body.w, max(body.h, floor - body.y)), s.blocks, palette)

    def _card(self, c: Canvas, r: Rect, name: str) -> Rect:
        c.rect(r, self.t.surface, name, rounded=True)
        return r.inset(CARD_PAD_X * self.f.scale, CARD_PAD_Y * self.f.scale)

    def _two_content(self, c: Canvas, s: SlideModel) -> None:
        columns = [col for col in (_lines(s.left), _lines(s.right)) if col]
        if len(columns) < 2:
            self._bullets(c, s.model_copy(update={"bullets": s.bullets or s.left or s.right}))
            return
        self._headline(c, self._title_text(s))
        body = self._lead(c, s)
        gap = GAP * self.f.scale
        pad_x, pad_y = CARD_PAD_X * self.f.scale, CARD_PAD_Y * self.f.scale
        inner_w = (body.w - gap) / 2 - 2 * pad_x
        contents = [self._bullet_paras(col, base=20) for col in columns]
        height = max(content_height(p, inner_w) for p in contents) + 2 * pad_y
        height = min(body.h, max(height, body.h * 0.45))
        row = Rect(body.x, body.y + (body.h - height) / 2, body.w, height)
        for i, (paras, r) in enumerate(zip(contents, row.split_h(2, gap), strict=True)):
            inner = self._card(c, r, f"decor:card-{i + 1}")
            c.text(inner, paras, self._body_style(), name=f"content:column-{i + 1}", min_pt=MIN_BODY)

    def _references(self, c: Canvas, entries: list[str]) -> None:
        self._headline(c, "Sources & References")
        cols = 1 if len(entries) <= 8 else 2
        per = -(-len(entries) // cols)
        for i, r in enumerate(self.f.body.split_h(cols, GAP * self.f.scale)):
            chunk = entries[i * per : (i + 1) * per]
            if chunk:
                c.text(
                    r,
                    [BulletPara(self._p(e, 12, after=6)) for e in chunk],
                    TextStyle(self.t.text),
                    name=f"content:references-{i + 1}",
                    min_pt=MIN_CAPTION,
                )

    def _fill_photo(self, c: Canvas, photo: Any) -> bool:
        """Put the slide's photo into the template's picture frame; True when one was filled."""
        photos = self.slots.get("photos") or []
        frames = self.slots.get("frames") or []
        samples = self.slots.get("samples") or []
        if not photos and samples:
            return swap_photo(c.slide, samples[0], photo)
        if not photos and not frames:
            return False
        frame = photos[0] if photos else frames[0]
        if photos:
            try:  # keeps the placeholder's own shape (hexagon, circle) and crops the photo to fill it
                pic = frame.insert_picture(str(photo.path))
                pic.name = "content:image"
                pic._element.nvPicPr.cNvPr.set("descr", photo.alt_text[:500])
                return True
            except Exception as exc:
                logger.info("Photo placeholder fill failed (%s); placing the photo over it", exc)
        r = Rect(frame.left / EMU_PER_INCH, frame.top / EMU_PER_INCH, frame.width / EMU_PER_INCH, frame.height / EMU_PER_INCH)
        if not photos:  # a drawn frame stays visible as a border around the photo
            r = r.inset(r.w * 0.04, r.w * 0.04)
        c.picture(r, photo.path, name="content:image", alt=photo.alt_text)
        if photos:
            frame._element.getparent().remove(frame._element)
        return True

    # ---------------------------------------------------------------- template fidelity

    def _readable_span(self, c: Canvas, shape) -> None:
        """Narrow a text box to the stretch of it where its text colour is readable on what lies behind.

        Designers let a one-word sample title straddle two panels (yellow | dark gradient); a real, longer title
        set in that box would run from one into the other and half of it would disappear. The box keeps the
        readable stretch around its alignment anchor (or the longest readable stretch).
        """
        if shape.left is None or shape.width is None:
            return
        runs = [r for p in shape.text_frame.paragraphs for r in p.runs]
        try:
            color = str(runs[0].font.color.rgb) if runs and runs[0].font.color.type is not None else self.t.text
        except AttributeError:
            color = self.t.text
        x0, w = int(shape.left), int(shape.width)
        y = int(shape.top) + int(shape.height) // 2
        n = 40
        ok = []
        for i in range(n):
            x = x0 + int(w * (i + 0.5) / n)
            under = color_under(c.slide, (x, y, 1, 1), stop_at=shape._element) or "FFFFFF"
            ok.append(contrast_ratio(color, under) >= 3)
        if all(ok) or not any(ok):
            return
        algn = getattr(shape.text_frame.paragraphs[0].alignment, "name", "") or ""
        anchor = {"CENTER": n // 2, "RIGHT": n - 1}.get(algn, 0)
        runs_ok, start = [], None
        for i, flag in enumerate(ok + [False]):
            if flag and start is None:
                start = i
            elif not flag and start is not None:
                runs_ok.append((start, i - 1))
                start = None
        span = next((r for r in runs_ok if r[0] <= anchor <= r[1]), max(runs_ok, key=lambda r: r[1] - r[0]))
        if (span[1] - span[0] + 1) / n < 0.3:
            return
        pad = int(self.tpl.design.slide_width * 0.02)
        left = x0 + int(w * span[0] / n) + (pad if span[0] > 0 else 0)
        right = x0 + int(w * (span[1] + 1) / n) - (pad if span[1] < n - 1 else 0)
        set_geometry(shape, x=left, w=max(right - left, int(w * 0.3)))

    def _fit_frame(self, ph, frame, title) -> None:
        """A decorative frame around a text slot (a pill behind a subtitle) is resized with its text: the text
        gets the width it needs on one line (up to the title's width) and the frame keeps its padding around it."""
        if frame is None:
            return
        from app.services.layout.text import measure

        pad_l, pad_r = int(ph.left) - int(frame.left), int(frame.left) + int(frame.width) - (int(ph.left) + int(ph.width))
        pad_t = int(ph.top) - int(frame.top)
        pad_b = int(frame.top) + int(frame.height) - (int(ph.top) + int(ph.height))
        tf = ph.text_frame
        runs = [r for p in tf.paragraphs for r in p.runs]
        if not runs:
            return
        size = runs[0].font.size.pt if runs[0].font.size else 18
        para = Para(tf.text.replace("\n", " "), size, bold=bool(runs[0].font.bold), font=runs[0].font.name)
        margins = int(tf.margin_left) + int(tf.margin_right)
        limit = max(int(ph.width), int(title.width) if title is not None else int(self.tpl.design.slide_width * 0.8))
        line = measure([para], 10**9).height_emu
        width = int(ph.width)
        while width < limit and measure([para], width - margins).height_emu > line * 1.2:
            width = min(limit, width + int(EMU_PER_INCH * 0.25))
        center = int(ph.left) + int(ph.width) // 2
        left = max(int(self.tpl.design.slide_width * 0.03), center - width // 2)
        set_geometry(ph, x=left, w=width)
        set_geometry(frame, x=left - max(pad_l, 0), w=width + max(pad_l, 0) + max(pad_r, 0),
                     y=int(ph.top) - max(pad_t, 0), h=int(ph.height) + max(pad_t, 0) + max(pad_b, 0))

    @staticmethod
    def _clear_title(c: Canvas, shape) -> None:
        """Template accent lines drawn under a one-word sample title must not strike through ours."""
        top, bottom = int(shape.top), int(shape.top) + int(shape.height)
        left, right = int(shape.left), int(shape.left) + int(shape.width)
        # An accent rule is long and flat relative to the slide (often grouped with end caps, so not hairline thin).
        thin = max(EMU_PER_INCH * 0.12, int(c.slide.part.package.presentation_part.presentation.slide_height) * 0.03)
        for other in c.slide.shapes:
            if other is shape or other.name.startswith(("content:", "footer:")) or other.width is None:
                continue
            ox, oy, ow, oh = int(other.left), int(other.top), int(other.width), int(other.height)
            horizontal = oh <= thin and ow >= 6 * oh
            if horizontal and top + 0.15 * (bottom - top) < oy < bottom and ox < right and ox + ow > left:
                set_geometry(other, y=bottom + int(EMU_PER_INCH * 0.05))

    def _readable(self, c: Canvas, shape) -> None:
        """Keep the template's text colour unless it would be unreadable on what is really behind it."""
        runs = [r for p in shape.text_frame.paragraphs for r in p.runs]
        if not runs:
            return
        try:
            color = str(runs[0].font.color.rgb) if runs[0].font.color.type is not None else None
        except AttributeError:
            color = None
        box = (int(shape.left), int(shape.top), int(shape.width), int(shape.height))
        under = color_under(c.slide, box, stop_at=shape._element)
        if color and under and contrast_ratio(color, under) >= 3:
            return
        best = max((self.t.text, "FFFFFF", "1F2328"), key=lambda x: contrast_ratio(x, under or "FFFFFF"))
        for r in runs:
            r.font.color.rgb = rgb(best)


def photo_slide_indices(deck: DeckModel, template_path: Any, profile: Any = None) -> list[int]:
    """Indices in ``deck.slides`` whose cloned design has a photo frame, cover first."""
    import pptx

    from app.services.pptx_renderer import _is_slide_empty, _slide_design

    design = _slide_design(pptx.Presentation(str(template_path)), profile)
    if design is None or design.design_mode != "slides":
        return []
    kept = [i for i, s in enumerate(deck.slides) if not _is_slide_empty(s)]
    kinds = [slide_kind(deck.slides[i], n) for n, i in enumerate(kept)]
    bases = assign_bases(kinds, design)
    wanted = [i for i, base, kind in zip(kept, bases, kinds, strict=True) if base.photo_shapes or base.frame_shapes or getattr(base, "sample_photos", None)]
    return sorted(wanted, key=lambda i: 0 if slide_kind(deck.slides[i], kept.index(i)) == "cover" else 1)


def render_cloned(
    prs: Any,
    design: SlideDesign,
    deck: DeckModel,
    slides: list[SlideModel],
    source_entries: list[str],
    photos: list[Any] | None = None,
) -> CanvasLog:
    """Fill ``prs`` (opened from the template) with ``slides`` cloned from its designed sample slides."""
    log = CloneComposer(CloneContext.open(prs, design), tokens_from_template(design)).compose(
        prs, deck, slides, source_entries, photos
    )
    for issue in log.fit_issues:
        logger.warning("Template text fit: %s - %s", issue.shape_name, issue.detail)
    return log
