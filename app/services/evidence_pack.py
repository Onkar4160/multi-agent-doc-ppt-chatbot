"""Evidence Pack grounding service – turns research findings and KB chunks into atomic verifiable facts."""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field


class EvidenceItem(BaseModel):
    """An atomic factual statement grounded in a specific source."""

    id: int = Field(description="Unique 1-based sequential ID in the evidence pack.")
    text: str = Field(description="Atomic factual statement text.")
    source_id: int = Field(description="Underlying source ID.")


def _split_into_sentences(text: str) -> list[str]:
    """Simple heuristic sentence splitter (NO LLM call)."""
    cleaned = text.strip()
    if not cleaned:
        return []
    # Split on sentence-ending punctuation followed by whitespace
    sentences = re.split(r"(?<=[.!?])\s+", cleaned)
    return [s.strip() for s in sentences if s.strip()]


def _chunk_sentences(sentences: list[str], max_chars: int = 200) -> list[str]:
    """Group sentences into 1-2 sentence pieces, capping each at ~max_chars."""
    pieces: list[str] = []
    i = 0
    while i < len(sentences):
        # Try taking 2 sentences first
        if i + 1 < len(sentences):
            pair = f"{sentences[i]} {sentences[i+1]}"
            if len(pair) <= max_chars:
                pieces.append(pair)
                i += 2
                continue

        # Single sentence piece
        s = sentences[i]
        if len(s) > max_chars:
            s = s[:max_chars].rstrip()
            if not s.endswith((".", "!", "?")):
                s = s + "..."
        pieces.append(s)
        i += 1

    return pieces


def build_evidence_pack(
    findings: list[Any] | None = None,
    kb_hits: list[dict[str, Any]] | None = None,
    start_id: int = 1,
    max_items: int = 24,
    max_per_source: int = 3,
) -> list[EvidenceItem]:
    """Aggregate web findings (atomic) and KB hits (split heuristically) into an evidence pack.

    Args:
        findings: List of FindingItem or dicts from web research (used as-is).
        kb_hits: List of raw KB chunk dicts (split into 1-2 sentence pieces, capped ~200 chars).
        start_id: Starting integer ID for evidence items (default 1).
        max_items: Maximum total evidence items (default 24).
        max_per_source: Maximum items per source ID (default 3).

    Returns:
        List of EvidenceItem with sequential IDs.
    """
    evidence_items: list[EvidenceItem] = []
    curr_id = start_id
    source_counts: dict[int, int] = {}

    # 1. KB Hits: split raw chunks into 1-2 sentence pieces
    if kb_hits:
        for hit in kb_hits:
            if len(evidence_items) >= max_items:
                break
            sid = hit.get("source_id", 0) if isinstance(hit, dict) else getattr(hit, "source_id", 0)
            if source_counts.get(sid, 0) >= max_per_source:
                continue

            raw_text = (
                hit.get("text") or hit.get("content") or ""
                if isinstance(hit, dict)
                else getattr(hit, "text", "")
            )
            sentences = _split_into_sentences(raw_text)
            pieces = _chunk_sentences(sentences, max_chars=200)
            for piece in pieces:
                if len(evidence_items) >= max_items:
                    break
                if source_counts.get(sid, 0) >= max_per_source:
                    break
                if piece:
                    evidence_items.append(
                        EvidenceItem(id=curr_id, text=piece, source_id=sid)
                    )
                    source_counts[sid] = source_counts.get(sid, 0) + 1
                    curr_id += 1

    # 2. Web findings: already atomic, use as-is
    if findings:
        for f in findings:
            if len(evidence_items) >= max_items:
                break
            if isinstance(f, dict):
                text = (f.get("text") or "").strip()
                sids = f.get("source_ids") or []
                sid = sids[0] if sids else f.get("source_id", 0)
            else:
                text = getattr(f, "text", "").strip()
                sids = getattr(f, "source_ids", None)
                sid = sids[0] if sids else getattr(f, "source_id", 0)

            if source_counts.get(sid, 0) >= max_per_source:
                continue

            if text:
                evidence_items.append(
                    EvidenceItem(id=curr_id, text=text, source_id=sid or 0)
                )
                source_counts[sid] = source_counts.get(sid, 0) + 1
                curr_id += 1

    return evidence_items


def format_evidence_pack(items: list[EvidenceItem]) -> str:
    """Format evidence items into a numbered plain-text block for prompt injection."""
    if not items:
        return "(No evidence items available)"
    return "\n".join(f"[{item.id}] {item.text}" for item in items)
