"""Copy-fitting for slides: headlines and points shortened at natural boundaries, citations shown once, figures
written the way people read them (ADR-037).

A slide that ends a headline with "most Test…" or shows "34357 runs" or "[2]. [2]" reads as machine output. These
helpers never cut mid-clause with an ellipsis: they end at the last clause boundary that keeps the sentence whole.
"""

from __future__ import annotations

import re

_CITE = re.compile(r"\s*(?:\[\d+(?:\s*[,;]\s*\d+)*\]\s*)+\.?")
_BOUNDARY = re.compile(r"\s*(?:[,;:–—(]|\s-\s|\b(?:and|which|while|with|as|because|including|where|whereas|but)\b)\s*", re.I)
_TRAILING = {"and", "or", "of", "the", "a", "an", "to", "in", "on", "for", "with", "by", "at", "as", "from", "that",
             "which", "while", "most", "its", "their", "is", "are", "was", "were"}


def strip_citations(text: str) -> str:
    """Text without bracketed citation markers ('[2]', '[2][5]', '[2, 5]'); the renderer adds them once."""
    return re.sub(r"\s{2,}", " ", _CITE.sub(" ", text or "")).strip()


def smart_trim(text: str, max_words: int = 14, *, min_words: int = 5) -> str:
    """At most ``max_words`` words, ending at a clause boundary where possible; never an ellipsis."""
    clean = re.sub(r"\s+", " ", (text or "").replace("…", "").strip()).rstrip(".")
    words = clean.split()
    if len(words) <= max_words:
        return clean
    head = " ".join(words[:max_words + 1])
    candidates = [head[:m.start()].strip() for m in _BOUNDARY.finditer(head)]
    candidates = [c for c in candidates if min_words <= len(c.split()) <= max_words]
    # The earliest boundary that keeps most of the point: later commas are usually inside a list.
    enough = max(min_words, -(-max_words * 55 // 100))
    best = next((c for c in candidates if len(c.split()) >= enough), candidates[-1] if candidates else None)
    out = best or " ".join(words[:max_words])
    out = out.rstrip(" ,;:–—(")
    tail = out.split()
    while len(tail) > min_words and tail[-1].lower().strip(",.;:") in _TRAILING:
        tail.pop()
    out = " ".join(tail)
    if out.count("(") > out.count(")"):
        out = out[: out.rfind("(")].rstrip()
    return out


def format_number(value: float) -> str:
    """34357 -> '34,357'; 1500000 -> '1.5 million'; 0.42 -> '0.42'; 2026 stays '2026' (years are not grouped)."""
    if value != value:  # NaN
        return ""
    v = abs(value)
    sign = "-" if value < 0 else ""
    if v.is_integer() and 1800 <= v <= 2100:
        return f"{int(value)}"
    for limit, word in ((1e12, "trillion"), (1e9, "billion"), (1e6, "million")):
        if v >= limit:
            return f"{sign}{v / limit:.3g} {word}"
    if v.is_integer():
        return f"{sign}{int(v):,}"
    return f"{sign}{v:,.2f}".rstrip("0").rstrip(".")


_RAW_NUMBER = re.compile(r"(?<![\w.,])(\d{5,})(?![\w.,])")


def group_digits(text: str) -> str:
    """Thousands separators for long bare integers inside text ('34357 runs' -> '34,357 runs')."""
    return _RAW_NUMBER.sub(lambda m: f"{int(m.group(1)):,}", text or "")
