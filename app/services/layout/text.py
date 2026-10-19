"""Text measurement and fitting in EMU (ADR-010). Shared by the renderer and the validator."""

from __future__ import annotations

from dataclasses import dataclass, replace
from functools import lru_cache

from PIL import ImageFont

from app.services.layout.fonts import resolve_font

EMU_PER_INCH = 914400
EMU_PER_PT = 12700
_SCALE = 4  # load fonts at 4x point size for sub-point precision
# PowerPoint's single line pitch is ~1.2x the font size for common fonts.
LINE_PITCH = 1.2
# Average advance (em fraction) when no font file is available; deliberately generous.
_FALLBACK_ADVANCE = 0.55


@dataclass(frozen=True)
class Para:
    text: str
    size_pt: float
    bold: bool = False
    font: str | None = None
    indent_emu: int = 0
    space_before_pt: float = 0.0
    space_after_pt: float = 0.0
    line_spacing: float = 1.0  # multiple of single spacing


@dataclass(frozen=True)
class Measurement:
    height_emu: int
    lines: int
    widest_word_overflow: bool
    estimated: bool  # True when the heuristic (no font file) was used


@lru_cache(maxsize=256)
def _font(family: str | None, bold: bool, size_pt: float):
    path = resolve_font(family, bold)
    if path is None:
        return None
    try:
        return ImageFont.truetype(str(path), size=max(1, round(size_pt * _SCALE)))
    except OSError:
        return None


def text_width_pt(
    text: str, size_pt: float, *, bold: bool = False, font: str | None = None
) -> tuple[float, bool]:
    f = _font(font, bold, size_pt)
    if f is None:
        factor = 1.08 if bold else 1.0
        return len(text) * size_pt * _FALLBACK_ADVANCE * factor, True
    return f.getlength(text) / _SCALE, False


def _wrap_count(para: Para, width_pt: float) -> tuple[int, bool, bool]:
    """Number of wrapped lines, whether a single word exceeds the width, whether estimated."""
    estimated = False
    lines = 0
    word_overflow = False
    for raw_line in para.text.split("\n") or [""]:
        words = raw_line.split()
        if not words:
            lines += 1
            continue
        current = ""
        count = 1
        for word in words:
            candidate = f"{current} {word}" if current else word
            w, est = text_width_pt(candidate, para.size_pt, bold=para.bold, font=para.font)
            estimated |= est
            if w <= width_pt or not current:
                if not current:
                    ww, est = text_width_pt(word, para.size_pt, bold=para.bold, font=para.font)
                    estimated |= est
                    word_overflow |= ww > width_pt
                current = candidate
            else:
                count += 1
                current = word
                ww, est = text_width_pt(word, para.size_pt, bold=para.bold, font=para.font)
                word_overflow |= ww > width_pt
        lines += count
    return lines, word_overflow, estimated


def measure(paras: list[Para], width_emu: int) -> Measurement:
    total_pt = 0.0
    lines = 0
    overflow = False
    estimated = False
    for i, p in enumerate(paras):
        avail_pt = max(1.0, (width_emu - p.indent_emu) / EMU_PER_PT)
        n, word_over, est = _wrap_count(p, avail_pt)
        lines += n
        overflow |= word_over
        estimated |= est
        total_pt += n * p.size_pt * LINE_PITCH * p.line_spacing
        if i > 0:
            total_pt += p.space_before_pt
        if i < len(paras) - 1:
            total_pt += p.space_after_pt
    return Measurement(round(total_pt * EMU_PER_PT), lines, overflow, estimated)


def fits(paras: list[Para], width_emu: int, height_emu: int) -> bool:
    m = measure(paras, width_emu)
    return m.height_emu <= height_emu and not m.widest_word_overflow


def scale(paras: list[Para], factor: float) -> list[Para]:
    return [
        replace(
            p,
            size_pt=round(p.size_pt * factor, 1),
            space_after_pt=round(p.space_after_pt * factor, 1),
            space_before_pt=round(p.space_before_pt * factor, 1),
        )
        for p in paras
    ]


def fit_paragraphs(
    paras: list[Para], width_emu: int, height_emu: int, *, min_pt: float
) -> tuple[list[Para], bool]:
    """Shrink all paragraphs proportionally (1pt steps on the largest) until they fit or hit ``min_pt``.

    ``min_pt`` applies to the smallest paragraph, so hierarchy is preserved. Returns the paragraphs at
    the chosen size and whether they fit.
    """
    if not paras:
        return paras, True
    if fits(paras, width_emu, height_emu):
        return paras, True
    largest = max(p.size_pt for p in paras)
    smallest = min(p.size_pt for p in paras)
    floor_factor = min_pt / smallest if smallest > 0 else 1.0
    size = largest
    while True:
        size -= 1
        factor = size / largest
        if factor < floor_factor:
            out = scale(paras, max(floor_factor, 0.0)) if floor_factor < 1 else paras
            return out, fits(out, width_emu, height_emu)
        candidate = scale(paras, factor)
        if fits(candidate, width_emu, height_emu):
            return candidate, True
