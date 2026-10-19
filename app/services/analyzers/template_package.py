"""Office template packages (.potx, .dotx, .xltx) opened as regular documents (FR-TPL-1).

python-pptx and python-docx refuse template packages because their main part has a *template* content type.
The package is otherwise identical, so a copy with the regular content type is analysed and rendered instead.
"""

from __future__ import annotations

import hashlib
import shutil
import tempfile
import zipfile
from pathlib import Path

_TEMPLATE_TYPES = {
    ".potx": ("presentationml.template.main+xml", "presentationml.presentation.main+xml", ".pptx"),
    ".dotx": ("wordprocessingml.template.main+xml", "wordprocessingml.document.main+xml", ".docx"),
    ".xltx": ("spreadsheetml.template.main+xml", "spreadsheetml.sheet.main+xml", ".xlsx"),
}


def regular_package(path: str | Path) -> Path:
    """``path`` itself for regular documents; for template packages, a cached regular copy."""
    src = Path(path)
    spec = _TEMPLATE_TYPES.get(src.suffix.lower())
    if spec is None:
        return src
    old, new, ext = spec
    digest = hashlib.sha256(src.read_bytes()).hexdigest()[:16]
    out = Path(tempfile.gettempdir()) / "template_packages" / f"{src.stem}_{digest}{ext}"
    if out.exists():
        return out
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(out.suffix + ".part")
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            data = zin.read(item.filename)
            if item.filename == "[Content_Types].xml":
                data = data.replace(old.encode(), new.encode())
            zout.writestr(item, data)
    shutil.move(str(tmp), str(out))
    return out


def file_sha256(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()
