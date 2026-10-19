"""Resolve font families to font files for measurement (ADR-010).

Order: exact family on this machine → metric-compatible substitute → None (caller uses the
average-advance heuristic and marks measurements as estimated).
"""

from __future__ import annotations

import os
import sys
from functools import lru_cache
from pathlib import Path

# family -> (regular, bold) candidate file names, most specific first
_FILES: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "calibri": (("calibri.ttf", "Carlito-Regular.ttf"), ("calibrib.ttf", "Carlito-Bold.ttf")),
    "cambria": (("cambria.ttc", "Caladea-Regular.ttf"), ("cambriab.ttf", "Caladea-Bold.ttf")),
    "arial": (
        ("arial.ttf", "LiberationSans-Regular.ttf", "Arial.ttf"),
        ("arialbd.ttf", "LiberationSans-Bold.ttf", "Arial Bold.ttf"),
    ),
    "times new roman": (
        ("times.ttf", "LiberationSerif-Regular.ttf"),
        ("timesbd.ttf", "LiberationSerif-Bold.ttf"),
    ),
    "courier new": (("cour.ttf", "LiberationMono-Regular.ttf"), ("courbd.ttf", "LiberationMono-Bold.ttf")),
    "georgia": (("georgia.ttf",), ("georgiab.ttf",)),
    "segoe ui": (("segoeui.ttf",), ("segoeuib.ttf",)),
    "verdana": (("verdana.ttf", "DejaVuSans.ttf"), ("verdanab.ttf", "DejaVuSans-Bold.ttf")),
}
_GENERIC = (
    ("DejaVuSans.ttf", "arial.ttf", "LiberationSans-Regular.ttf"),
    ("DejaVuSans-Bold.ttf", "arialbd.ttf", "LiberationSans-Bold.ttf"),
)


def _font_dirs() -> list[Path]:
    dirs: list[Path] = []
    if sys.platform.startswith("win"):
        windir = os.environ.get("WINDIR", "C:/Windows")
        dirs += [Path(windir) / "Fonts", Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft/Windows/Fonts"]
    elif sys.platform == "darwin":
        dirs += [
            Path("/System/Library/Fonts"),
            Path("/Library/Fonts"),
            Path.home() / "Library/Fonts",
            Path("/Applications/Microsoft PowerPoint.app/Contents/Resources/DFonts"),
        ]
    else:
        dirs += [Path("/usr/share/fonts"), Path("/usr/local/share/fonts"), Path.home() / ".fonts"]
    return [d for d in dirs if d.is_dir()]


@lru_cache(maxsize=1)
def _index() -> dict[str, Path]:
    found: dict[str, Path] = {}
    for d in _font_dirs():
        for path in d.rglob("*"):
            if path.suffix.lower() in (".ttf", ".ttc", ".otf"):
                found.setdefault(path.name.lower(), path)
    return found


@lru_cache(maxsize=64)
def resolve_font(family: str | None, bold: bool = False) -> Path | None:
    index = _index()
    regular, bolds = _FILES.get((family or "").strip().lower(), _GENERIC)
    for name in (bolds if bold else regular) + (regular if bold else ()):
        if name.lower() in index:
            return index[name.lower()]
    for name in _GENERIC[1 if bold else 0]:
        if name.lower() in index:
            return index[name.lower()]
    return None
