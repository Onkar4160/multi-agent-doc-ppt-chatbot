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


# Fonts embedded in templates (ppt/fonts/*.fntdata): typeface -> {"regular"|"bold": extracted TrueType file}.
# PowerPoint renders template text with these, so measuring with them makes fitting match what users see.
_EMBEDDED: dict[str, dict[str, Path]] = {}
_REGISTERED: set[str] = set()
_STYLE_WORDS = ("bold", "semibold", "light", "medium", "regular", "black", "extrabold", "thin")


def _key(family: str) -> str:
    return " ".join(family.strip().lower().split())


def _base_family(family: str) -> tuple[str, bool]:
    """'Montserrat Bold' -> ('montserrat', True); style words at the end are a weight, not a family."""
    words = _key(family).split()
    bold = False
    while len(words) > 1 and words[-1] in _STYLE_WORDS:
        bold = bold or words[-1] in ("bold", "semibold", "extrabold", "black")
        words.pop()
    return " ".join(words), bold


def _eot_font_data(blob: bytes) -> bytes | None:
    """TrueType/OpenType bytes inside an EOT (.fntdata) container; None when compressed or unreadable."""
    import struct

    if len(blob) < 16:
        return None
    size, data_size, _version, flags = struct.unpack("<IIII", blob[:16])
    if blob[:4] in (b"\x00\x01\x00\x00", b"true"):
        return blob  # stored as a plain font
    if size != len(blob) or not 0 < data_size <= len(blob) or flags & 0x4:  # MTX-compressed data is not read
        return None
    data = blob[-data_size:]
    if flags & 0x10000000:  # XOR-obfuscated
        data = bytes(b ^ 0x50 for b in data)
    # Only TrueType outlines: PowerPoint does not render embedded CFF ("OTTO") fonts and substitutes another
    # font for them, so their own metrics would under-measure what users see.
    return data if data[:4] in (b"\x00\x01\x00\x00", b"true") else None


def register_embedded_fonts(pptx_path: str | Path) -> int:
    """Extract a template's embedded fonts into the storage cache and use them for measurement; returns how many."""
    import hashlib
    import io
    import re
    import zipfile

    from PIL import ImageFont

    path = Path(pptx_path)
    try:
        stamp = f"{path.resolve()}:{path.stat().st_mtime_ns}"
    except OSError:
        return 0
    if stamp in _REGISTERED:
        return 0
    _REGISTERED.add(stamp)
    try:
        from app.core.config import get_settings

        cache = Path(get_settings().storage_dir) / "fonts" / hashlib.sha1(stamp.encode()).hexdigest()[:12]
        added = 0
        with zipfile.ZipFile(path) as z:
            pres = z.read("ppt/presentation.xml").decode("utf-8", "ignore")
            rels = z.read("ppt/_rels/presentation.xml.rels").decode("utf-8", "ignore")
            targets = dict(re.findall(r'Id="(rId\d+)"[^>]*Target="([^"]+)"', rels))
            targets.update({k: v for v, k in re.findall(r'Target="([^"]+)"[^>]*Id="(rId\d+)"', rels)})
            for block in re.findall(r"<p:embeddedFont>(.*?)</p:embeddedFont>", pres, re.S):
                typeface = re.search(r'typeface="([^"]+)"', block)
                if not typeface:
                    continue
                for style, rid in re.findall(r"<p:(regular|bold|italic|boldItalic) r:id=\"(rId\d+)\"", block):
                    if style not in ("regular", "bold") or rid not in targets:
                        continue
                    data = _eot_font_data(z.read("ppt/" + targets[rid].lstrip("/").removeprefix("ppt/")))
                    if data is None:
                        continue
                    try:
                        family, weight = ImageFont.truetype(io.BytesIO(data), 12).getname()
                    except OSError:
                        continue
                    cache.mkdir(parents=True, exist_ok=True)
                    out = cache / f"{_key(typeface.group(1)).replace(' ', '_')}_{style}.ttf"
                    out.write_bytes(data)
                    is_bold = style == "bold" or "bold" in (weight or "").lower()
                    for name in {_key(typeface.group(1)), _key(family), _key(f"{family} {weight}")}:
                        _EMBEDDED.setdefault(name, {})["bold" if is_bold else "regular"] = out
                        _EMBEDDED[name].setdefault("regular", out)
                    added += 1
        if added:
            resolve_font.cache_clear()
            has_exact_metrics.cache_clear()
            from app.services.layout import text

            text._font.cache_clear()
        return added
    except Exception:  # noqa: BLE001 - measurement falls back to substitutes
        return 0


@lru_cache(maxsize=1)
def _family_index() -> dict[str, dict[str, Path]]:
    """Installed fonts by their own family name (read from the file), for families the table above lacks."""
    from PIL import ImageFont

    out: dict[str, dict[str, Path]] = {}
    for path in _index().values():
        try:
            family, style = ImageFont.truetype(str(path), 12).getname()
        except Exception:  # noqa: BLE001 - unreadable font files are skipped
            continue
        bold = "bold" in (style or "").lower() or "black" in (style or "").lower()
        slot = out.setdefault(_key(family), {})
        slot.setdefault("bold" if bold else "regular", path)
    return out


@lru_cache(maxsize=64)
def has_exact_metrics(family: str | None) -> bool:
    """True when the family itself (embedded in the template or installed) is available for measuring."""
    key = _key(family or "")
    if not key:
        return False
    base, _ = _base_family(key)
    if key in _EMBEDDED or base in _EMBEDDED:
        return True
    files = _FILES.get(key) or _FILES.get(base)
    if files:
        return any(name.lower() in _index() for name in files[0][:1] + files[1][:1])
    return bool(_family_index().get(key) or _family_index().get(base))


@lru_cache(maxsize=64)
def resolve_font(family: str | None, bold: bool = False) -> Path | None:
    key = _key(family or "")
    base, styled_bold = _base_family(key) if key else ("", False)
    want = "bold" if (bold or styled_bold) else "regular"
    for name in (key, base):
        if name and name in _EMBEDDED:
            files = _EMBEDDED[name]
            return files.get(want) or files.get("regular")
    index = _index()
    if key and key not in _FILES and base not in _FILES:
        for name in (key, base):
            files = _family_index().get(name)
            if files:
                return files.get(want) or files.get("regular") or next(iter(files.values()))
    regular, bolds = _FILES.get(key, _FILES.get(base, _GENERIC))
    for name in (bolds if want == "bold" else regular) + (regular if want == "bold" else ()):
        if name.lower() in index:
            return index[name.lower()]
    for name in _GENERIC[1 if want == "bold" else 0]:
        if name.lower() in index:
            return index[name.lower()]
    return None
