"""Pictures and free space on designed slides (ADR-037).

- ``swap_photo``: a template's sample photograph is replaced by a topic photo inside the same shape, so the frame
  the designer drew (arch, oval, hexagon, rounded panel) is kept and the photo is cropped to fill it.
- ``clear_of_artwork``: the content area is narrowed until it keeps a gap to the artwork beside it, measured on the
  slide being drawn, so text never runs under or against a picture, a leaf or a panel edge, on any template.
- ``split_for_photo`` / ``round_corners``: a side photo panel for text slides on designs without a photo slot.
- ``_slot_frame``: the decorative shape around a text slot (a pill behind a subtitle) that must follow its text.
"""

from __future__ import annotations

import logging
from typing import Any

from lxml import etree

from app.services.layout.text import EMU_PER_INCH
from app.services.renderers.pptx_canvas import Rect

logger = logging.getLogger(__name__)

_A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
_R_EMBED = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}embed"
_OURS = ("content:", "decor:", "footer:")


def _box_in(shape: Any) -> tuple[float, float, float, float] | None:
    if shape.left is None or shape.width is None:
        return None
    return (int(shape.left) / EMU_PER_INCH, int(shape.top) / EMU_PER_INCH,
            int(shape.width) / EMU_PER_INCH, int(shape.height) / EMU_PER_INCH)


def _has_text(shape: Any) -> bool:
    from app.services.analyzers.slide_design import all_text

    return bool(all_text(shape))


def clear_of_artwork(slide: Any, rect: Rect, scale: float, slide_area_emu: float) -> Rect:
    """``rect`` (inches) narrowed so it keeps a gap to the template artwork it touches or overlaps."""
    gap = 0.3 * scale
    full = slide_area_emu / EMU_PER_INCH ** 2
    out = rect
    obstacles = []
    for shape in slide.shapes:
        if (shape.name or "").startswith(_OURS) or _has_text(shape):
            continue
        box = _box_in(shape)
        if box is None or box[2] * box[3] >= 0.5 * full:
            continue
        x, y, w, h = box
        contains = x <= rect.x and y <= rect.y and x + w >= rect.right_x and y + h >= rect.bottom_y
        if contains:
            continue  # a panel behind the content area
        obstacles.append(box)
    for x, y, w, h in sorted(obstacles, key=lambda b: -(b[2] * b[3])):
        ex0, ey0, ex1, ey1 = x - gap, y - gap, x + w + gap, y + h + gap
        if ex1 <= out.x or ex0 >= out.right_x or ey1 <= out.y or ey0 >= out.bottom_y:
            continue
        cx = x + w / 2
        if cx >= out.x + out.w / 2:
            cand = Rect(out.x, out.y, ex0 - out.x, out.h)
        else:
            cand = Rect(ex1, out.y, out.right_x - ex1, out.h)
        if cand.w >= 0.55 * rect.w:
            out = cand
            continue
        cy = y + h / 2
        cand = Rect(out.x, out.y, out.w, ey0 - out.y) if cy >= out.y + out.h / 2 else Rect(out.x, ey1, out.w, out.bottom_y - ey1)
        if cand.h >= 0.5 * rect.h:
            out = cand
        # otherwise the artwork spans the middle of the area: it is the template's backdrop, keep the area
    return out


def split_for_photo(body: Rect, scale: float) -> tuple[Rect, Rect]:
    """Text on the left (about 58 %), a photo panel on the right with a comfortable gap."""
    gap = 0.4 * scale
    photo_w = body.w * 0.4
    text = Rect(body.x, body.y, body.w - photo_w - gap, body.h)
    h = min(body.h, photo_w * 1.15)
    photo = Rect(body.right_x - photo_w, body.y + (body.h - h) / 2, photo_w, h)
    return text, photo


def round_corners(pic: Any, radius: int = 6000) -> None:
    """Rounded corners on a picture (prstGeom roundRect) — softer than a hard rectangle on most designs."""
    geom = pic._element.spPr.find(f"{_A}prstGeom")
    if geom is None:
        return
    geom.set("prst", "roundRect")
    av = geom.find(f"{_A}avLst")
    if av is None:
        av = etree.SubElement(geom, f"{_A}avLst")
    for gd in list(av):
        av.remove(gd)
    gd = etree.SubElement(av, f"{_A}gd")
    gd.set("name", "adj")
    gd.set("fmla", f"val {radius}")


def _owner_ratio(blip: Any) -> float | None:
    """Width/height of the shape a picture fill belongs to."""
    node = blip.getparent()
    while node is not None and node.tag.split("}")[-1] not in ("pic", "sp"):
        node = node.getparent()
    if node is None:
        return None
    ext = node.find(f".//{_A}xfrm/{_A}ext")
    if ext is None or not int(ext.get("cy", "0")):
        return None
    return int(ext.get("cx")) / int(ext.get("cy"))


def swap_photo(slide: Any, shape: Any, photo: Any) -> bool:
    """Put ``photo`` into the template's sample-photo shape, cropped to fill it; True when done."""
    from PIL import Image

    from app.services.analyzers.slide_design import _photographic, photo_blips

    try:
        target = None
        for blip in photo_blips(shape):
            rid = blip.get(_R_EMBED)
            if rid and _photographic(slide.part.related_part(rid).blob):
                target = blip
                break
        if target is None:
            return False
        _part, rid = slide.part.get_or_add_image_part(str(photo.path))
        target.set(_R_EMBED, rid)
        fill = target.getparent()
        for old in fill.findall(f"{_A}srcRect"):
            fill.remove(old)
        ratio = _owner_ratio(target)
        with Image.open(photo.path) as img:
            img_ratio = img.width / img.height
        src = etree.Element(f"{_A}srcRect")
        if ratio:
            if img_ratio > ratio:  # wider than the frame: crop the sides
                cut = int((1 - ratio / img_ratio) / 2 * 100000)
                src.set("l", str(cut))
                src.set("r", str(cut))
            elif img_ratio < ratio:  # taller: crop more from the bottom than the top
                cut = 1 - img_ratio / ratio
                src.set("t", str(int(cut * 0.35 * 100000)))
                src.set("b", str(int(cut * 0.65 * 100000)))
        fill.insert(list(fill).index(target) + 1, src)
        for c_nv in shape._element.iter("{http://schemas.openxmlformats.org/presentationml/2006/main}cNvPr"):
            c_nv.set("descr", (photo.alt_text or "")[:500])
            break
        shape.name = "content:image"
        return True
    except Exception as exc:  # a photo that cannot be placed leaves the template's own picture
        logger.info("Sample photo swap skipped: %s", exc)
        return False


def _slot_frame(slide: Any, slot: Any, design: Any) -> Any:
    """The text-free shape framing ``slot`` (contains its centre, a few times its size), or None."""
    if slot.left is None or slot.width is None:
        return None
    sx, sy, sw, sh = int(slot.left), int(slot.top), int(slot.width), int(slot.height)
    cx, cy = sx + sw / 2, sy + sh / 2
    area = max(sw * sh, 1)
    full = design.slide_width * design.slide_height
    best = None
    for shape in slide.shapes:
        if shape is slot or shape.left is None or shape.width is None or _has_text(shape):
            continue
        x, y, w, h = int(shape.left), int(shape.top), int(shape.width), int(shape.height)
        if not (x <= cx <= x + w and y <= cy <= y + h):
            continue
        if not (0.5 * area <= w * h <= 6 * area) or w * h >= 0.3 * full:
            continue
        if best is None or w * h < int(best.width) * int(best.height):
            best = shape
    return best
