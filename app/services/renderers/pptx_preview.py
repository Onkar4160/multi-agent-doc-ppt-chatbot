"""Approximate PNG previews of PPTX slides without LibreOffice (ported from DocForge ``render/pptx/preview.py``).

Draws backgrounds, rectangles/ovals/chevrons, connectors, wrapped text with real font metrics, native
charts (bar/column/line/pie approximations) and tables. It is a geometry preview, not a faithful
renderer: effects, gradients, pictures and SmartArt are drawn as outlines.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from pptx import Presentation
from pptx.enum.chart import XL_CHART_TYPE
from pptx.enum.dml import MSO_FILL
from pptx.enum.shapes import MSO_AUTO_SHAPE_TYPE, MSO_SHAPE_TYPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN

from app.services.layout.backdrop import _background, _leaves
from app.services.layout.fonts import resolve_font
from app.services.layout.text import LINE_PITCH

_A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
_R_EMBED = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}embed"
_P = "http://schemas.openxmlformats.org/presentationml/2006/main"
_BAR = {XL_CHART_TYPE.BAR_CLUSTERED, XL_CHART_TYPE.BAR_STACKED}
_PIE = {XL_CHART_TYPE.PIE, XL_CHART_TYPE.DOUGHNUT}
_LINE = {XL_CHART_TYPE.LINE_MARKERS, XL_CHART_TYPE.LINE}


def _hex(color: str | None, default: str = "000000") -> tuple[int, int, int]:
    c = color or default
    return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)


def _solid(fill) -> str | None:
    try:
        return str(fill.fore_color.rgb) if fill.type == MSO_FILL.SOLID else None
    except (AttributeError, TypeError):
        return None


def _fill_of(shape, owner) -> str | None:
    """Solid fill of a shape, theme colours (accent1, bg1, ...) resolved through the master's theme."""
    from app.services.layout.backdrop import _scheme_color

    el = shape._element
    sp_pr = next((c for c in el if c.tag.endswith("}spPr")), None)
    node = sp_pr.find(f"{_A}solidFill") if sp_pr is not None else None
    if node is None and (sp_pr is None or sp_pr.find(f"{_A}noFill") is None):
        node = el.find(f"./{{{_P}}}style/{_A}fillRef")  # fill from the shape style (theme colour)
    if node is None or not len(node):
        return None
    color = node[0]
    if color.tag == f"{_A}srgbClr":
        return (color.get("val") or "").upper() or None
    if color.tag == f"{_A}schemeClr":
        return _scheme_color(owner, color.get("val"))
    return None


def _freeform(draw, shape, box, fill) -> None:
    """Freeform outline from its path points (curves approximated by their end points)."""
    for path in shape._element.iter(f"{_A}path"):
        w, h = int(path.get("w") or 0), int(path.get("h") or 0)
        if not w or not h:
            continue
        pts = [
            (box[0] + int(pt.get("x")) / w * (box[2] - box[0]), box[1] + int(pt.get("y")) / h * (box[3] - box[1]))
            for pt in path.iter(f"{_A}pt")
        ]
        if len(pts) >= 3:
            draw.polygon(pts, fill=fill)
            return
    draw.rectangle(box, fill=fill)


class _Painter:
    def __init__(self, width_px: int, slide_w: int, slide_h: int) -> None:
        self.scale = width_px / slide_w  # px per EMU
        self.size = (width_px, round(slide_h * self.scale))
        self.pt = self.scale * 12700  # px per point

    def box(self, shape) -> tuple[int, int, int, int]:
        x, y = int(shape.left or 0) * self.scale, int(shape.top or 0) * self.scale
        return (
            round(x),
            round(y),
            round(x + int(shape.width or 0) * self.scale),
            round(y + int(shape.height or 0) * self.scale),
        )

    def font(self, family: str | None, bold: bool, size_pt: float):
        path = resolve_font(family, bold)
        px = max(6, round(size_pt * self.pt))
        return ImageFont.truetype(str(path), px) if path else ImageFont.load_default(px)


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, width: float) -> list[str]:
    lines: list[str] = []
    for raw in text.split("\n"):
        current = ""
        for word in raw.split():
            candidate = f"{current} {word}".strip()
            if draw.textlength(candidate, font=font) <= width or not current:
                current = candidate
            else:
                lines.append(current)
                current = word
        lines.append(current)
    return lines


def _text(draw, painter: _Painter, shape, box, mark_overflow: bool = True) -> None:
    tf = shape.text_frame
    x0 = box[0] + int(tf.margin_left) * painter.scale
    x1 = box[2] - int(tf.margin_right) * painter.scale
    y0 = box[1] + int(tf.margin_top) * painter.scale
    y1 = box[3] - int(tf.margin_bottom) * painter.scale
    blocks = []
    for p in tf.paragraphs:
        runs = p.runs
        if not runs:
            continue
        f = runs[0].font
        size = f.size.pt if f.size else 18
        font = painter.font(f.name, bool(f.bold), size)
        try:
            color = str(f.color.rgb)
        except AttributeError:
            color = "000000"
        p_pr = p._p.pPr
        indent = int(p_pr.get("marL", "0")) * painter.scale if p_pr is not None else 0
        bullet = (
            p_pr is not None
            and p_pr.find("{http://schemas.openxmlformats.org/drawingml/2006/main}buChar") is not None
        )
        text = "".join(r.text for r in runs)
        lines = _wrap(draw, text, font, x1 - x0 - indent) if tf.word_wrap is not False else [text]
        after = (p.space_after.pt if p.space_after else 0) * painter.pt
        blocks.append((lines, font, size, color, indent, bullet, p.alignment, after))
    total = sum(len(b[0]) * b[2] * LINE_PITCH * painter.pt + b[7] for b in blocks)
    anchor = tf.vertical_anchor
    y = (
        y0
        if anchor in (None, MSO_ANCHOR.TOP)
        else (y1 - total if anchor == MSO_ANCHOR.BOTTOM else y0 + (y1 - y0 - total) / 2)
    )
    for lines, font, size, color, indent, bullet, align, after in blocks:
        pitch = size * LINE_PITCH * painter.pt
        for li, line in enumerate(lines):
            width = draw.textlength(line, font=font)
            if align == PP_ALIGN.CENTER:
                x = x0 + (x1 - x0 - width) / 2
            elif align == PP_ALIGN.RIGHT:
                x = x1 - width
            else:
                x = x0 + indent
            if bullet and li == 0:
                r = max(2, size * painter.pt * 0.12)
                cy = y + pitch * 0.55
                draw.ellipse((x - indent * 0.6 - r, cy - r, x - indent * 0.6 + r, cy + r), fill=_hex(color))
            draw.text((x, y + (pitch - size * painter.pt) * 0.5), line, font=font, fill=_hex(color))
            y += pitch
        y += after
    if mark_overflow and y > y1 + 2:  # overflow marker, so problems are visible in previews
        draw.rectangle(box, outline=(220, 0, 0), width=3)


def _chart(draw, painter: _Painter, shape, box) -> None:
    chart = shape.chart
    plot = chart.plots[0]
    cats = list(plot.categories)
    series = [list(s.values) for s in plot.series]
    colors = []
    for s in plot.series:
        try:
            colors.append(_hex(str(s.format.fill.fore_color.rgb)))
        except (AttributeError, TypeError):
            colors.append((80, 110, 160))
    x0, y0, x1, y1 = box[0] + 30, box[1] + 30, box[2] - 10, box[3] - 30
    draw.rectangle(box, outline=(225, 228, 232))
    if not cats or not series:
        return
    kind = chart.chart_type
    if kind in _PIE:
        total = sum(v or 0 for v in series[0]) or 1
        start = -90.0
        palette = [(30, 80, 140), (70, 130, 180), (240, 165, 65), (90, 100, 110), (20, 40, 70)]
        cx, cy, r = (x0 + x1) / 2, (y0 + y1) / 2, min(x1 - x0, y1 - y0) / 2
        for i, v in enumerate(series[0]):
            sweep = 360 * (v or 0) / total
            draw.pieslice(
                (cx - r, cy - r, cx + r, cy + r), start, start + sweep, fill=palette[i % len(palette)]
            )
            start += sweep
        return
    peak = max((v or 0) for s in series for v in s) or 1
    n = len(cats)
    if kind in _LINE:
        for si, s in enumerate(series):
            pts = [
                (x0 + (i + 0.5) * (x1 - x0) / n, y1 - (v or 0) / peak * (y1 - y0)) for i, v in enumerate(s)
            ]
            draw.line(pts, fill=colors[si % len(colors)], width=3)
        return
    horizontal = kind in _BAR
    slot = ((y1 - y0) if horizontal else (x1 - x0)) / n
    bw = slot * 0.6 / len(series)
    for si, s in enumerate(series):
        for i, v in enumerate(s):
            frac = (v or 0) / peak
            if horizontal:
                top = y0 + i * slot + slot * 0.2 + si * bw
                draw.rectangle((x0, top, x0 + frac * (x1 - x0), top + bw), fill=colors[si % len(colors)])
            else:
                left = x0 + i * slot + slot * 0.2 + si * bw
                draw.rectangle((left, y1 - frac * (y1 - y0), left + bw, y1), fill=colors[si % len(colors)])


def _table(draw, painter: _Painter, shape, box) -> None:
    table = shape.table
    y = box[1]
    for row in table.rows:
        h = int(row.height) * painter.scale
        x = box[0]
        for c_i, cell in enumerate(row.cells):
            w = int(table.columns[c_i].width) * painter.scale
            fill = _solid(cell.fill) or "FFFFFF"
            draw.rectangle((x, y, x + w, y + h), fill=_hex(fill), outline=(210, 214, 220))
            runs = cell.text_frame.paragraphs[0].runs
            if runs:
                f = runs[0].font
                font = painter.font(f.name, bool(f.bold), f.size.pt if f.size else 12)
                try:
                    color = str(f.color.rgb)
                except AttributeError:
                    color = "000000"
                draw.text((x + 6, y + (h - font.size) / 2), cell.text_frame.text, font=font, fill=_hex(color))
            x += w
        y += h


def _blip_fill(canvas: Image.Image, slide, shape, box) -> bool:
    """Paste a picture-filled shape (template artwork) with its transparency; False if it has none."""
    import io

    blip = next(shape._element.iter(f"{_A}blip"), None)
    rid = blip.get(_R_EMBED) if blip is not None else None
    if not rid:
        return False
    try:
        art = Image.open(io.BytesIO(slide.part.related_part(rid).blob)).convert("RGBA")
    except Exception:  # noqa: BLE001 - unreadable art is skipped in previews
        return True
    size = (max(1, box[2] - box[0]), max(1, box[3] - box[1]))
    if size[0] > 4000 or size[1] > 4000:
        return True
    art = art.resize(size)
    canvas.paste(art, (box[0], box[1]), art)
    return True


def _picture(canvas: Image.Image, shape, box) -> None:
    """Paste the picture with its crop applied; outline only if the image cannot be decoded."""
    import io

    try:
        pic = Image.open(io.BytesIO(shape.image.blob)).convert("RGBA")  # logos keep their transparency
    except Exception:
        ImageDraw.Draw(canvas).rectangle(box, outline=(150, 150, 150), width=2)
        return
    w, h = pic.size
    crop = (
        round(w * shape.crop_left),
        round(h * shape.crop_top),
        round(w * (1 - shape.crop_right)),
        round(h * (1 - shape.crop_bottom)),
    )
    size = (max(1, box[2] - box[0]), max(1, box[3] - box[1]))
    art = pic.crop(crop).resize(size)
    canvas.paste(art, (box[0], box[1]), art)


def render_previews(
    pptx_path: Path, out_dir: Path, *, width_px: int = 1280, mark_overflow: bool = True
) -> list[Path]:
    """PNG per slide. ``mark_overflow`` outlines text that seems too long (approximate metrics: QA only)."""
    prs = Presentation(str(pptx_path))
    painter = _Painter(width_px, int(prs.slide_width), int(prs.slide_height))
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for index, slide in enumerate(prs.slides, start=1):
        # Backgrounds and artwork inherited from the layout and master show through (theme colours resolved).
        full = (0, 0, int(prs.slide_width), int(prs.slide_height))
        background = _background(slide, full) or "FFFFFF"  # never touch slide.background: it adds a <p:bg>
        img = Image.new("RGB", painter.size, _hex(background))
        draw = ImageDraw.Draw(img)
        layout = slide.slide_layout
        layers = [
            (layout.slide_master, [s for s in layout.slide_master.shapes if not s.is_placeholder]),
            (layout, [s for s in layout.shapes if not s.is_placeholder]),
            (slide, list(slide.shapes)),
        ]
        leaves = [(owner, leaf) for owner, shapes in layers for leaf in _leaves(shapes)]
        for owner, (shape, (ax, ay, aw, ah)) in leaves:
            box = (
                round(ax * painter.scale),
                round(ay * painter.scale),
                round((ax + aw) * painter.scale),
                round((ay + ah) * painter.scale),
            )
            if shape.shape_type != MSO_SHAPE_TYPE.PICTURE and _blip_fill(img, owner, shape, box):
                pass
            elif shape.shape_type == MSO_SHAPE_TYPE.FREEFORM and _fill_of(shape, owner):
                _freeform(draw, shape, box, _hex(_fill_of(shape, owner)))
            if shape.shape_type == MSO_SHAPE_TYPE.AUTO_SHAPE:
                fill = _fill_of(shape, owner)
                try:
                    kind = shape.auto_shape_type
                except (ValueError, AttributeError):
                    kind = None
                if fill:
                    if kind == MSO_AUTO_SHAPE_TYPE.OVAL:
                        draw.ellipse(box, fill=_hex(fill))
                    elif kind == MSO_AUTO_SHAPE_TYPE.ROUNDED_RECTANGLE:
                        draw.rounded_rectangle(
                            box, radius=min(box[2] - box[0], box[3] - box[1]) * 0.08, fill=_hex(fill)
                        )
                    elif kind == MSO_AUTO_SHAPE_TYPE.CHEVRON:
                        x0, y0, x1, y1 = box
                        mid = (y0 + y1) / 2
                        draw.polygon(
                            [
                                (x0, y0),
                                (x1 - (x1 - x0) * 0.4, y0),
                                (x1, mid),
                                (x1 - (x1 - x0) * 0.4, y1),
                                (x0, y1),
                                (x0 + (x1 - x0) * 0.4, mid),
                            ],
                            fill=_hex(fill),
                        )
                    else:
                        draw.rectangle(box, fill=_hex(fill))
            elif shape.shape_type == MSO_SHAPE_TYPE.LINE or shape.shape_type == MSO_SHAPE_TYPE.FREEFORM:
                pass
            elif shape.shape_type == MSO_SHAPE_TYPE.PICTURE:
                _picture(img, shape, box)
            if getattr(shape, "has_chart", False):
                _chart(draw, painter, shape, box)
            elif getattr(shape, "has_table", False):
                _table(draw, painter, shape, box)
            elif shape.shape_type is None or getattr(shape, "begin_x", None) is not None:
                try:
                    color = _hex(str(shape.line.color.rgb))
                except (AttributeError, TypeError):
                    color = (120, 120, 120)
                draw.line(
                    (
                        painter.scale * shape.begin_x,
                        painter.scale * shape.begin_y,
                        painter.scale * shape.end_x,
                        painter.scale * shape.end_y,
                    ),
                    fill=color,
                    width=3,
                )
            if (
                getattr(shape, "has_text_frame", False)
                and shape.has_text_frame
                and shape.text_frame.text.strip()
            ):
                _text(draw, painter, shape, box, mark_overflow)
        path = out_dir / f"slide-{index:02d}.png"
        img.save(path)
        paths.append(path)
    return paths
