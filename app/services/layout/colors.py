"""Contrast maths and design tokens for templates whose design lives on the sample slides.

Ported from DocForge ``design/system.py`` (``tokens_from_template``, ``darken_until``, ``tint``).
Colours are 6-digit hex strings without ``#``.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.models.template_profile import SlideDesign


@dataclass(frozen=True)
class SlideTokens:
    background: str
    surface: str  # card tint on light slides
    text: str
    text_muted: str
    primary: str
    secondary: str
    heading_font: str | None
    body_font: str | None


def relative_luminance(hex_color: str) -> float:
    r, g, b = (int(hex_color[i : i + 2], 16) / 255 for i in (0, 2, 4))

    def chan(c: float) -> float:
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    return 0.2126 * chan(r) + 0.7152 * chan(g) + 0.0722 * chan(b)


def contrast_ratio(a: str, b: str) -> float:
    la, lb = sorted((relative_luminance(a), relative_luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def tint(hex_color: str, amount: float) -> str:
    """Mix a colour with white; amount 0 = unchanged, 1 = white."""
    rgb = [int(hex_color[i : i + 2], 16) for i in (0, 2, 4)]
    return "".join(f"{round(c + (255 - c) * amount):02X}" for c in rgb)


def darken_until(hex_color: str, background: str, target: float) -> str:
    """Darken a colour in small steps until it reaches ``target`` contrast on ``background``."""
    rgb = [int(hex_color[i : i + 2], 16) for i in (0, 2, 4)]
    for _ in range(20):
        candidate = "".join(f"{c:02X}" for c in rgb)
        if contrast_ratio(candidate, background) >= target:
            return candidate
        rgb = [round(c * 0.88) for c in rgb]
    return "".join(f"{c:02X}" for c in rgb)


def _theme_primary(theme: dict[str, str], background: str) -> str:
    """First theme accent readable on the background (headings use it), else the dark text colour."""
    accents = [theme[k] for k in ("accent1", "accent2", "accent3", "accent4", "accent5", "accent6") if k in theme]
    readable = [a for a in accents if contrast_ratio(a, background) >= 3.0]
    return readable[0] if readable else theme.get("dk2") or theme.get("dk1") or "1F2328"


def tokens_from_template(design: SlideDesign) -> SlideTokens:
    """Tokens from the slide artwork: content sits on the light areas, so colours contrast with those."""
    theme = design.theme_colors
    regions = [b.region_color for b in design.slide_bases if b.kind == "content" and b.region_color]
    light = [c for c in regions if contrast_ratio(c, "000000") >= 10]
    background = max(set(light), key=light.count) if light else "FFFFFF"
    text = design.slide_text_color or theme.get("dk1") or "1F2328"
    if contrast_ratio(text, background) < 4.5:
        text = "1F2328"

    def readable(color: str) -> str:
        for bg in set(light) | {background}:
            color = darken_until(color, bg, 3.2)
        return color

    accents = [readable(a) for a in design.slide_palette] or [readable(_theme_primary(theme, background))]
    primary = accents[0]
    secondary = accents[1] if len(accents) > 1 else readable(tint(primary, 0.3))
    return SlideTokens(
        background=background,
        surface=tint(primary, 0.88),
        text=text,
        text_muted=darken_until("5B6470", background, 4.5),
        primary=primary,
        secondary=secondary,
        heading_font=design.slide_fonts.get("heading") or design.theme_fonts.get("major"),
        body_font=design.slide_fonts.get("body") or design.theme_fonts.get("minor"),
    )
