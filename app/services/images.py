"""Openly licensed photos for template photo frames (ported from DocForge ``media/images.py``).

Openverse (openly licensed, keyless) by default; Pexels when ``PEXELS_API_KEY`` is set. Every photo keeps its
credit line, which the deck prints in the speaker notes and on the Sources slide (CC BY attribution).
"""

from __future__ import annotations

import io
import logging
import re
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from PIL import Image

logger = logging.getLogger(__name__)

OPENVERSE_URL = "https://api.openverse.org/v1/images/"
PEXELS_URL = "https://api.pexels.com/v1/search"
MAX_IMAGE_BYTES = 8 * 1024 * 1024
MIN_WIDTH = 800
_HEADERS = {"User-Agent": "multi-agent-doc-ppt-chatbot/1.0 (document generation; contact: admin@localhost)"}


class ImageError(RuntimeError):
    pass


@dataclass
class ImageCandidate:
    url: str
    width: int
    height: int
    credit: str
    landing_url: str
    fallback_url: str | None = None  # e.g. Openverse's proxy when the origin host refuses
    title: str = ""


@dataclass
class PhotoAsset:
    path: Path
    credit: str
    landing_url: str
    alt_text: str


class ImageProvider(ABC):
    name: str
    client: httpx.Client

    @abstractmethod
    def search(self, query: str, *, count: int = 5, wide: bool = True) -> list[ImageCandidate]: ...

    def download(self, candidate: ImageCandidate) -> bytes:
        try:
            return self._fetch(candidate.url)
        except ImageError:
            if not candidate.fallback_url:
                raise
            return self._fetch(candidate.fallback_url)

    def _fetch(self, url: str) -> bytes:
        try:
            with self.client.stream("GET", url, follow_redirects=True) as resp:
                if not resp.is_success:
                    raise ImageError(f"image download failed ({resp.status_code})")
                data = bytearray()
                for chunk in resp.iter_bytes():
                    data.extend(chunk)
                    if len(data) > MAX_IMAGE_BYTES:
                        raise ImageError("image too large")
        except httpx.HTTPError as exc:
            raise ImageError(f"image download failed: {exc.__class__.__name__}") from exc
        return bytes(data)

    def close(self) -> None:
        self.client.close()


class OpenverseProvider(ImageProvider):
    name = "openverse"

    def __init__(self, client: httpx.Client | None = None) -> None:
        self.client = client or httpx.Client(timeout=30, headers=_HEADERS)

    def search(self, query: str, *, count: int = 5, wide: bool = True) -> list[ImageCandidate]:
        params = {"q": query, "page_size": str(count), "license_type": "commercial,modification", "mature": "false"}
        if wide:
            params["aspect_ratio"] = "wide"
        try:
            resp = self.client.get(OPENVERSE_URL, params=params)
        except httpx.HTTPError as exc:
            raise ImageError(f"openverse request failed: {exc.__class__.__name__}") from exc
        if not resp.is_success:
            raise ImageError(f"openverse error {resp.status_code}")
        out = []
        for r in resp.json().get("results", []):
            if not r.get("url"):
                continue
            license_text = f"CC {r.get('license', '').upper()} {r.get('license_version') or ''}".strip()
            if r.get("license") in ("cc0", "pdm"):
                license_text = "public domain"
            out.append(
                ImageCandidate(
                    r["url"],
                    int(r.get("width") or 0),
                    int(r.get("height") or 0),
                    f"Photo: {r.get('creator') or 'unknown'} ({r.get('provider') or 'Openverse'}), {license_text}",
                    r.get("foreign_landing_url") or r["url"],
                    f"{r['thumbnail']}?full_size=true" if r.get("thumbnail") else None,
                    r.get("title") or "",
                )
            )
        return out


class PexelsProvider(ImageProvider):
    name = "pexels"

    def __init__(self, api_key: str, client: httpx.Client | None = None) -> None:
        self.client = client or httpx.Client(timeout=30, headers={**_HEADERS, "Authorization": api_key})

    def search(self, query: str, *, count: int = 5, wide: bool = True) -> list[ImageCandidate]:
        params = {"query": query, "per_page": str(count)}
        if wide:
            params["orientation"] = "landscape"
        try:
            resp = self.client.get(PEXELS_URL, params=params)
        except httpx.HTTPError as exc:
            raise ImageError(f"pexels request failed: {exc.__class__.__name__}") from exc
        if not resp.is_success:
            raise ImageError(f"pexels error {resp.status_code}")
        return [
            ImageCandidate(
                p["src"].get("large2x") or p["src"]["original"],
                int(p.get("width") or 0),
                int(p.get("height") or 0),
                f"Photo: {p.get('photographer', 'unknown')} (Pexels)",
                p.get("url", ""),
                title=p.get("alt") or "",
            )
            for p in resp.json().get("photos", [])
            if p.get("src")
        ]


def build_image_provider(settings: Any) -> ImageProvider | None:
    if not settings.images_enabled or settings.mock_llm:
        return None
    if settings.pexels_api_key:
        return PexelsProvider(settings.pexels_api_key)
    return OpenverseProvider()


def relevance(query: str, title: str) -> int:
    words = {w for w in re.findall(r"[a-z]{3,}", query.lower())} - {"the", "and", "with", "for"}
    return sum(1 for w in words if w in title.lower())


_FILLER = {"in", "of", "the", "and", "for", "a", "an", "on", "to", "with", "about", "vs", "from"}


def _queries(query: str) -> list[str]:
    """The query, then its first three and two content words."""
    words = [w for w in re.findall(r"[A-Za-z0-9\-]+", query) if w.lower() not in _FILLER]
    out = [query] + [" ".join(words[:n]) for n in (3, 2) if len(words) > n]
    return list(dict.fromkeys(q for q in out if q))


def _normalise(data: bytes) -> tuple[bytes, str] | None:
    """Re-encode to JPEG/PNG, drop metadata, cap size at 2400 px; None if not a usable image."""
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except Exception:
        return None
    if img.width < MIN_WIDTH // 2:
        return None
    img.thumbnail((2400, 2400))
    out = io.BytesIO()
    if img.mode in ("RGBA", "LA", "P"):
        img.convert("RGBA").save(out, format="PNG", optimize=True)
        return out.getvalue(), ".png"
    img.convert("RGB").save(out, format="JPEG", quality=86, optimize=True)
    return out.getvalue(), ".jpg"


def resolve_photo(
    query: str,
    provider: ImageProvider,
    out_dir: Path,
    *,
    used: set[str],
    cache: dict[str, list[ImageCandidate]],
    alt_text: str = "",
) -> PhotoAsset | None:
    """Best unused photo for ``query``: most relevant first (query words in the title), then ~1800 px wide."""
    candidates: list[ImageCandidate] = []
    for q in _queries(query):  # long titles often match nothing; shorter forms of the topic do
        try:
            if q not in cache:
                cache[q] = provider.search(q, count=8)
        except ImageError as exc:
            logger.warning("Photo search skipped: %s", exc)
            return None
        candidates = [c for c in cache[q] if c.url not in used]
        if candidates:
            break
    for c in sorted(candidates, key=lambda c: (-relevance(query, c.title), abs((c.width or 1600) - 1800))):
        if (c.width and c.width < MIN_WIDTH) or c.url in used:
            continue
        try:
            normalised = _normalise(provider.download(c))
        except ImageError as exc:
            logger.info("Photo download skipped: %s", exc)
            continue
        if normalised is None:
            continue
        data, ext = normalised
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"photo_{uuid.uuid4().hex[:10]}{ext}"
        path.write_bytes(data)
        used.add(c.url)
        return PhotoAsset(path=path, credit=c.credit, landing_url=c.landing_url, alt_text=alt_text or query)
    logger.info("No suitable photo found for '%s'", query[:50])
    return None


def fetch_deck_photos(
    deck: Any, template_path: Any, profile: Any, out_dir: Path, *, provider: ImageProvider | None = None
) -> dict[int, PhotoAsset]:
    """Photos for the slides whose template design has a photo frame (cover first), no photo used twice.

    Returns ``{index in deck.slides: PhotoAsset}``; empty when images are off or the template has no frames.
    """
    from app.core.config import get_settings
    from app.services.renderers.pptx_clone import photo_slide_indices

    settings = get_settings()
    own_provider = provider is None
    provider = provider or build_image_provider(settings)
    if provider is None:
        return {}
    try:
        wanted = photo_slide_indices(deck, template_path, profile)[: settings.max_deck_images]
        topic = " ".join(deck.title.replace(":", " ").split()[:6])
        used: set[str] = set()
        cache: dict[str, list[ImageCandidate]] = {}
        photos: dict[int, PhotoAsset] = {}
        for index in wanted:
            slide = deck.slides[index]
            photo = resolve_photo(topic, provider, out_dir, used=used, cache=cache, alt_text=f"Photo illustrating {slide.title}")
            if photo is not None:
                photos[index] = photo
        return photos
    except Exception as exc:  # photos are a bonus: a deck without them is still a deck
        logger.warning("Deck photos skipped: %s", exc)
        return {}
    finally:
        if own_provider:
            provider.close()


def add_document_photos(doc: Any, out_dir: Path, *, max_photos: int = 2, provider: ImageProvider | None = None) -> int:
    """Insert up to ``max_photos`` credited photos as figures (after the first paragraph of the first and the middle
    section). Returns how many were added; a document without photos is still a document."""
    from app.core.config import get_settings
    from app.models.blocks import ImageBlock
    from app.models.document_model import FigureBlock

    settings = get_settings()
    own_provider = provider is None
    provider = provider or build_image_provider(settings)
    if provider is None or not doc.sections or max_photos <= 0:
        return 0
    try:
        topic = " ".join(doc.title.replace(":", " ").split()[:6])
        targets = list(dict.fromkeys([0, len(doc.sections) // 2]))[:max_photos]
        used: set[str] = set()
        cache: dict[str, list[ImageCandidate]] = {}
        added = 0
        for idx in targets:
            section = doc.sections[idx]
            photo = resolve_photo(topic, provider, out_dir, used=used, cache=cache, alt_text=f"Photo illustrating {section.heading}")
            if photo is None:
                continue
            figure = FigureBlock(caption=f"{section.heading}. Photo: {photo.credit}",
                                 image=ImageBlock(description=photo.alt_text, alt_text=photo.alt_text,
                                                  asset_path=str(photo.path), credit=photo.credit))
            section.blocks.insert(min(1, len(section.blocks)), figure)
            added += 1
        return added
    except Exception as exc:
        logger.warning("Document photos skipped: %s", exc)
        return 0
    finally:
        if own_provider:
            provider.close()
