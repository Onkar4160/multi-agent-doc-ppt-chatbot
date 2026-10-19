"""Source evaluation: authority, recency and relevance (PRD FR-RES-6; ADR-006).

A source's existence is not proof. Each source gets an authority rating with a rationale the user can read,
so evidence from a government statistics office and from an anonymous blog are not weighed the same.
"""

from __future__ import annotations

import datetime as dt
import re
from urllib.parse import urlparse

_HIGH_SUFFIXES = (".gov", ".gov.uk", ".gov.in", ".gov.au", ".gc.ca", ".edu", ".ac.uk", ".ac.in", ".edu.au", ".int", ".europa.eu", ".mil")
_HIGH_DOMAINS = {
    "who.int", "un.org", "oecd.org", "worldbank.org", "imf.org", "iea.org", "irena.org", "nih.gov", "ncbi.nlm.nih.gov",
    "nature.com", "science.org", "sciencedirect.com", "springer.com", "link.springer.com", "wiley.com",
    "onlinelibrary.wiley.com", "ieee.org", "ieeexplore.ieee.org", "acm.org", "dl.acm.org", "thelancet.com", "nejm.org",
    "bmj.com", "cell.com", "pnas.org", "doi.org", "eurostat.ec.europa.eu", "ec.europa.eu", "europa.eu", "data.gov",
    "census.gov", "bls.gov", "ons.gov.uk", "rbi.org.in", "niti.gov.in", "mospi.gov.in",
}
_MEDIUM_DOMAINS = {
    "reuters.com", "bloomberg.com", "ft.com", "economist.com", "bbc.com", "bbc.co.uk", "nytimes.com", "wsj.com",
    "theguardian.com", "apnews.com", "mckinsey.com", "deloitte.com", "pwc.com", "bcg.com", "kpmg.com", "ey.com",
    "accenture.com", "gartner.com", "statista.com", "pewresearch.org", "brookings.edu", "rand.org", "hbr.org",
    "forbes.com", "techcrunch.com", "wired.com", "arstechnica.com", "theverge.com", "cnbc.com", "economictimes.indiatimes.com",
    "thehindu.com", "livemint.com", "wikipedia.org", "en.wikipedia.org", "britannica.com", "arxiv.org", "ourworldindata.org",
}
_LOW_HINTS = ("medium.com", "blogspot.", "wordpress.", "quora.com", "reddit.com", "linkedin.com/pulse", "youtube.com",
              "facebook.com", "twitter.com", "x.com", "tiktok.com", "substack.com", "pinterest.")


def domain_of(url: str | None) -> str:
    host = urlparse(url or "").netloc.lower()
    return host[4:] if host.startswith("www.") else host


def assess_authority(kind: str, url: str | None, venue: str | None = None, doi: str | None = None) -> tuple[str, str]:
    """(authority, rationale) for a source."""
    if kind == "paper":
        if doi or (venue and venue.lower() != "arxiv"):
            return "high", f"peer-reviewed or indexed publication{f' ({venue})' if venue else ''}"
        return "medium", "preprint (not yet peer reviewed)"
    if kind in ("user_file", "dataset"):
        return "high", "supplied by the user"
    if kind == "kb":
        return "medium", "internal knowledge base"
    host = domain_of(url)
    full = (url or "").lower()
    if not host:
        return "low", "no publisher could be identified"
    if any(hint in full for hint in _LOW_HINTS):
        return "low", f"user-generated or opinion platform ({host})"
    if host in _HIGH_DOMAINS or any(host.endswith(s) for s in _HIGH_SUFFIXES) or any(host.endswith("." + d) for d in _HIGH_DOMAINS):
        return "high", f"official, academic or intergovernmental publisher ({host})"
    if host in _MEDIUM_DOMAINS or any(host.endswith("." + d) for d in _MEDIUM_DOMAINS):
        return "medium", f"established news, research or reference publisher ({host})"
    return "low", f"publisher not recognised ({host}); use with care"


def year_of(published: str | None) -> int | None:
    m = re.search(r"(19|20)\d{2}", published or "")
    return int(m.group(0)) if m else None


def recency_score(published: str | None, today: dt.date | None = None) -> float:
    """1.0 for this year, falling to 0.2 for sources ten or more years old; 0.5 when undated."""
    year = year_of(published)
    if year is None:
        return 0.5
    age = max(0, (today or dt.date.today()).year - year)
    return max(0.2, 1.0 - 0.08 * age)


_STOP = {"the", "and", "for", "with", "from", "into", "what", "how", "why", "are", "does", "that", "this", "which",
         "about", "their", "they", "them", "have", "has", "will", "been", "were", "was", "its", "our", "your", "than",
         "more", "most", "also", "such", "these", "those", "who", "when", "where", "there", "then", "over", "under"}


def terms(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z][a-z0-9\-]{2,}", (text or "").lower()) if w not in _STOP}


def relevance(text: str, wanted: set[str]) -> float:
    if not wanted:
        return 0.0
    return len(terms(text) & wanted) / len(wanted)


_WEIGHT = {"high": 1.0, "medium": 0.7, "low": 0.4}


def rank_score(authority: str, published: str | None, text: str, wanted: set[str]) -> float:
    """Combined ordering key: relevance first, then authority, then recency."""
    return round(0.5 * relevance(text, wanted) + 0.35 * _WEIGHT.get(authority, 0.4) + 0.15 * recency_score(published), 4)
