"""How a version was rendered, so the next version looks the same (FR-EDIT-3, ADR-036).

An edit changes the content model; everything else that shaped the file — the template (or the generated design
and its hint) and the photos placed on slides or in the document — is stored with the version and reused, so a
one-bullet edit never restyles the deck or swaps its pictures. Photos are copied next to the version's file because
render outputs are temporary.
"""

from __future__ import annotations

import json
import logging
import shutil
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from app.core.config import get_settings

logger = logging.getLogger(__name__)


def output_dir() -> Path:
    """Where files are rendered before they become versions (inside storage, so tests stay isolated)."""
    path = Path(get_settings().storage_dir) / "outputs"
    path.mkdir(parents=True, exist_ok=True)
    return path


@dataclass
class RenderContext:
    template_path: str | None = None  # None with mode "generated": the no-template design system
    template_file_id: int | None = None
    mode: str = "template"  # template | generated
    design_hint: str = ""
    photos: dict[str, dict[str, str]] = field(default_factory=dict)  # slide node_id -> photo asset fields

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @classmethod
    def from_json(cls, text: str | None) -> RenderContext | None:
        if not text:
            return None
        try:
            data = json.loads(text)
            return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
        except (ValueError, TypeError) as exc:
            logger.warning("Stored render context unreadable; using defaults: %s", exc)
            return None

    def template(self) -> Path | None:
        """The template file, when it still exists (uploads can be deleted)."""
        if self.mode == "generated" or not self.template_path:
            return None
        path = Path(self.template_path)
        return path if path.exists() else None


def _media_dir(artifact_id: int) -> Path:
    path = Path(get_settings().storage_dir) / "artifacts" / str(artifact_id) / "media"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _keep(path: str | Path, artifact_id: int) -> str:
    """Copy a rendered asset next to the artifact's versions (idempotent) and return the kept path."""
    src = Path(path)
    media = _media_dir(artifact_id)
    if src.parent == media or not src.exists():
        return str(src)
    dest = media / src.name
    if not dest.exists():
        shutil.copy2(src, dest)
    return str(dest)


def deck_photos_by_node(deck: Any, photos: dict[int, Any], artifact_id: int) -> dict[str, dict[str, str]]:
    """{slide node_id: photo fields} with the photo files kept in the artifact's media folder."""
    out: dict[str, dict[str, str]] = {}
    for index, photo in (photos or {}).items():
        if index >= len(deck.slides) or photo is None or not deck.slides[index].node_id:
            continue
        out[deck.slides[index].node_id] = {
            "path": _keep(photo.path, artifact_id), "credit": photo.credit,
            "landing_url": photo.landing_url, "alt_text": photo.alt_text,
        }
    return out


def photos_for_deck(deck: Any, ctx: RenderContext | None) -> dict[int, Any]:
    """The stored photos of slides that still exist, by their index in ``deck.slides``."""
    from app.services.images import PhotoAsset

    if ctx is None:
        return {}
    out: dict[int, Any] = {}
    for i, slide in enumerate(deck.slides):
        data = ctx.photos.get(slide.node_id or "")
        if data and Path(data["path"]).exists():
            out[i] = PhotoAsset(path=Path(data["path"]), credit=data.get("credit", ""),
                                landing_url=data.get("landing_url", ""), alt_text=data.get("alt_text", ""))
    return out


def keep_document_figures(doc: Any, artifact_id: int) -> None:
    """Point the document's figure images at kept copies, so later versions can render them again."""
    for section in getattr(doc, "sections", []):
        for block in section.blocks:
            image = getattr(block, "image", None)
            if image is not None and image.asset_path:
                image.asset_path = _keep(image.asset_path, artifact_id)
