"""Progressive File Discovery Service.

Enables progressive file inspection:
1. Inspect metadata & high-level structure (sections, slide titles, sheet names)
2. Identify relevant regions based on requirements
3. Targeted extraction of specific chunks/pages/slides without context dumping.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any
from pydantic import BaseModel, Field

from app.services.parsers import parse_file, ParsedContent

logger = logging.getLogger(__name__)


class FileStructure(BaseModel):
    """High-level outline/structure of a file without full text dump."""

    file_name: str
    file_type: str
    item_count: int = Field(description="Page count, slide count, or sheet count.")
    outline: list[str] = Field(default_factory=list, description="Section headings, slide titles, or sheet names.")
    metadata: dict[str, Any] = Field(default_factory=dict)


class RelevantRegion(BaseModel):
    """Targeted region identified for extraction."""

    index: int
    title: str
    score: float = 1.0
    reason: str = ""


def inspect_structure(file_path: str | Path) -> FileStructure:
    """Inspect structure of any document without loading full body contents into memory."""
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")

    ext = path.suffix.lower()

    if ext == ".docx":
        import docx
        doc = docx.Document(path)
        headings = []
        for p in doc.paragraphs:
            if p.style and p.style.name.lower().startswith("heading") and p.text.strip():
                headings.append(p.text.strip())
        return FileStructure(
            file_name=path.name,
            file_type="docx",
            item_count=len(doc.paragraphs),
            outline=headings[:25],
            metadata={"paragraph_count": len(doc.paragraphs), "table_count": len(doc.tables)},
        )

    elif ext == ".pptx":
        import pptx
        prs = pptx.Presentation(path)
        slide_titles = []
        for s_idx, slide in enumerate(prs.slides, start=1):
            title = ""
            for shape in slide.shapes:
                if shape.has_text_frame and shape.text_frame.text.strip():
                    title = shape.text_frame.text.strip().split("\n")[0]
                    break
            slide_titles.append(title or f"Slide {s_idx}")
        return FileStructure(
            file_name=path.name,
            file_type="pptx",
            item_count=len(prs.slides),
            outline=slide_titles,
            metadata={"slide_count": len(prs.slides)},
        )

    elif ext == ".xlsx":
        from app.services.analyzers.xlsx_analyzer import analyze_xlsx
        info = analyze_xlsx(path)
        sheet_names = [s["name"] for s in info.get("sheets", [])]
        return FileStructure(
            file_name=path.name,
            file_type="xlsx",
            item_count=info.get("sheet_count", 0),
            outline=sheet_names,
            metadata={"total_formulas": info.get("total_formulas", 0)},
        )

    elif ext == ".pdf":
        import pymupdf
        doc = pymupdf.open(path)
        page_count = len(doc)
        toc = doc.get_toc()
        toc_headings = [entry[1] for entry in toc if len(entry) >= 2]
        doc.close()
        return FileStructure(
            file_name=path.name,
            file_type="pdf",
            item_count=page_count,
            outline=toc_headings[:25],
            metadata={"page_count": page_count},
        )

    else:
        parsed = parse_file(path)
        headings = [b.text for b in parsed.blocks if b.type == "heading"]
        return FileStructure(
            file_name=path.name,
            file_type=ext.lstrip("."),
            item_count=parsed.page_or_slide_count,
            outline=headings[:20],
        )


def discover_relevant_regions(
    structure: FileStructure,
    query_or_requirements: str | list[str],
    max_regions: int = 5,
) -> list[RelevantRegion]:
    """Identify which sections/slides/sheets are relevant to the query or plan requirements."""
    if isinstance(query_or_requirements, list):
        query_text = " ".join(query_or_requirements).lower()
    else:
        query_text = (query_or_requirements or "").lower()

    words = set(re.findall(r"\w{3,}", query_text))
    scored_regions: list[RelevantRegion] = []

    for idx, item in enumerate(structure.outline, start=1):
        item_words = set(re.findall(r"\w{3,}", item.lower()))
        common = words & item_words
        score = len(common) / max(len(words), 1) if words else 0.5
        if score > 0.1 or not words:
            scored_regions.append(RelevantRegion(
                index=idx,
                title=item,
                score=score,
                reason=f"Matched keywords: {list(common)}" if common else "Default discovery",
            ))

    scored_regions.sort(key=lambda r: r.score, reverse=True)
    return scored_regions[:max_regions] or [
        RelevantRegion(index=1, title=structure.outline[0] if structure.outline else "Overview", score=1.0)
    ]


def extract_targeted_content(
    file_path: str | Path,
    regions: list[RelevantRegion],
    max_chars: int = 4000,
) -> list[dict[str, Any]]:
    """Extract only the content blocks corresponding to identified regions."""
    parsed = parse_file(file_path)
    target_titles = {r.title.lower() for r in regions}
    results = []

    current_section = ""
    include_block = False

    for block in parsed.blocks:
        if block.type == "heading" or block.type == "slide_title":
            current_section = block.text
            include_block = any(t in block.text.lower() for t in target_titles)

        if include_block and block.text.strip():
            results.append({
                "region": current_section,
                "type": block.type,
                "text": block.text[:max_chars],
                "metadata": block.metadata,
            })

    if not results and parsed.blocks:
        # Fallback to first few blocks if exact title match didn't catch body
        for b in parsed.blocks[:5]:
            results.append({
                "region": "Summary",
                "type": b.type,
                "text": b.text[:1000],
                "metadata": b.metadata,
            })

    return results
