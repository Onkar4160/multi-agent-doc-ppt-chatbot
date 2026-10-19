"""Scholarly search: OpenAlex, Semantic Scholar and arXiv (ported from DocForge ``research/academic.py``).

Each works without an API key at a lower provider-imposed rate. Results use the same dict shape as the web
search (title, url, snippet, published_date) plus scholarly metadata; abstracts are the evidence text.
"""

from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ElementTree  # arXiv's own API feed; expat >= 2.4 guards entity expansion
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import httpx

logger = logging.getLogger(__name__)

OPENALEX_URL = "https://api.openalex.org/works"
S2_URL = "https://api.semanticscholar.org/graph/v1/paper/search"
ARXIV_URL = "https://export.arxiv.org/api/query"
_ATOM = "{http://www.w3.org/2005/Atom}"
_ARXIV_NS = "{http://arxiv.org/schemas/atom}"
_STOP = {"the", "and", "for", "with", "from", "into", "what", "how", "why", "are", "does", "that", "this", "which"}
_HEADERS = {"User-Agent": "multi-agent-doc-ppt-chatbot/1.0 (document generation research)"}


class AcademicSearchError(RuntimeError):
    pass


def _get(provider: str, url: str, *, client: httpx.Client | None = None, **kwargs) -> httpx.Response:
    try:
        if client is not None:
            resp = client.get(url, **kwargs)
        else:
            with httpx.Client(timeout=20.0, headers=_HEADERS, follow_redirects=True) as c:
                resp = c.get(url, **kwargs)
    except httpx.HTTPError as exc:
        raise AcademicSearchError(f"{provider} request failed: {exc.__class__.__name__}") from exc
    if resp.status_code == 429:
        raise AcademicSearchError(f"{provider} rate limit reached (add an API key to raise it)")
    if not resp.is_success:
        raise AcademicSearchError(f"{provider} error {resp.status_code}")
    return resp


def _abstract_from_index(index: dict[str, list[int]] | None) -> str:
    """OpenAlex stores abstracts as an inverted index: word -> positions."""
    if not index:
        return ""
    positions = [(pos, word) for word, poss in index.items() for pos in poss]
    return " ".join(word for _, word in sorted(positions))


def _doi_url(doi: str | None) -> str | None:
    if not doi:
        return None
    doi = doi.removeprefix("https://doi.org/").removeprefix("http://doi.org/")
    return f"https://doi.org/{doi}"


def search_openalex(query: str, max_results: int = 3, *, client: httpx.Client | None = None) -> list[dict[str, Any]]:
    params = {
        "search": query,
        "per_page": str(max(1, min(max_results, 25))),
        "filter": "has_abstract:true",
        "select": "id,doi,display_name,publication_year,publication_date,primary_location,authorships,"
        "cited_by_count,abstract_inverted_index,type",
    }
    data = _get("openalex", OPENALEX_URL, client=client, params=params).json()
    out = []
    for w in data.get("results", []):
        loc = w.get("primary_location") or {}
        source = loc.get("source") or {}
        url = _doi_url(w.get("doi")) or loc.get("landing_page_url") or w.get("id")
        abstract = _abstract_from_index(w.get("abstract_inverted_index"))
        if not url or not w.get("display_name") or not abstract:
            continue
        out.append({
            "title": w["display_name"],
            "url": url,
            "snippet": abstract[:600],
            "published_date": w.get("publication_date") or (str(w["publication_year"]) if w.get("publication_year") else None),
            "authors": [
                a["author"]["display_name"] for a in (w.get("authorships") or [])[:6]
                if a.get("author", {}).get("display_name")
            ],
            "venue": source.get("display_name"),
            "citations": w.get("cited_by_count"),
            "kind": "preprint" if w.get("type") == "preprint" else "paper",
            "provider": "openalex",
        })
    return out


def search_semantic_scholar(
    query: str, max_results: int = 3, *, api_key: str | None = None, client: httpx.Client | None = None
) -> list[dict[str, Any]]:
    params = {
        "query": query,
        "limit": str(max(1, min(max_results, 20))),
        "fields": "title,abstract,year,venue,authors,citationCount,url,externalIds,publicationDate,tldr",
    }
    headers = {"x-api-key": api_key} if api_key else {}
    data = _get("semantic_scholar", S2_URL, client=client, params=params, headers=headers).json()
    out = []
    for p in data.get("data", []) or []:
        abstract = p.get("abstract") or ((p.get("tldr") or {}).get("text")) or ""
        doi = (p.get("externalIds") or {}).get("DOI")
        url = _doi_url(doi) or p.get("url")
        if not url or not p.get("title") or not abstract:
            continue
        out.append({
            "title": p["title"],
            "url": url,
            "snippet": abstract[:600],
            "published_date": p.get("publicationDate") or (str(p["year"]) if p.get("year") else None),
            "authors": [a["name"] for a in (p.get("authors") or [])[:6] if a.get("name")],
            "venue": p.get("venue") or None,
            "citations": p.get("citationCount"),
            "kind": "paper",
            "provider": "semantic_scholar",
        })
    return out


def arxiv_query(text: str) -> str:
    words = [w for w in re.findall(r"[A-Za-z0-9\-]+", text.lower()) if len(w) > 3 and w not in _STOP]
    return " AND ".join(f"all:{w}" for w in words[:5]) or f"all:{text}"


def search_arxiv(query: str, max_results: int = 3, *, client: httpx.Client | None = None) -> list[dict[str, Any]]:
    params = {
        "search_query": arxiv_query(query),
        "start": "0",
        "max_results": str(max(1, min(max_results, 20))),
        "sortBy": "relevance",
    }
    resp = _get("arxiv", ARXIV_URL, client=client, params=params)
    try:
        root = ElementTree.fromstring(resp.content)
    except ElementTree.ParseError as exc:
        raise AcademicSearchError("arxiv returned malformed XML") from exc
    out = []
    for entry in root.findall(f"{_ATOM}entry"):
        title = " ".join((entry.findtext(f"{_ATOM}title") or "").split())
        summary = " ".join((entry.findtext(f"{_ATOM}summary") or "").split())
        url = entry.findtext(f"{_ATOM}id") or ""
        if not title or not url or not summary:
            continue
        journal = entry.findtext(f"{_ARXIV_NS}journal_ref")
        out.append({
            "title": title,
            "url": url,
            "snippet": summary[:600],
            "published_date": (entry.findtext(f"{_ATOM}published") or "")[:10] or None,
            "authors": [a.findtext(f"{_ATOM}name") or "" for a in entry.findall(f"{_ATOM}author")][:6],
            "venue": journal[:100] if journal else "arXiv",
            "citations": None,
            "kind": "paper" if journal else "preprint",
            "provider": "arxiv",
        })
    return out


def scholarly_title(item: dict[str, Any]) -> str:
    """'Title (Venue, 2024)': the Sources slide shows where and when a paper appeared."""
    year = (item.get("published_date") or "")[:4]
    where = ", ".join(x for x in (item.get("venue"), year) if x)
    return f"{item['title']} ({where})" if where else item["title"]


def search_scholarly(query: str, *, per_provider: int = 2, semantic_scholar_key: str | None = None) -> list[dict[str, Any]]:
    """Papers and preprints from all three indexes in parallel; a failing index is skipped, never fatal."""
    jobs = {
        "openalex": lambda: search_openalex(query, per_provider),
        "semantic_scholar": lambda: search_semantic_scholar(query, per_provider, api_key=semantic_scholar_key),
        "arxiv": lambda: search_arxiv(query, per_provider),
    }
    results: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
        futures = {name: pool.submit(job) for name, job in jobs.items()}
        for name, fut in futures.items():
            try:
                results.extend(fut.result(timeout=30))
            except Exception as exc:
                logger.warning("Scholarly search via %s skipped: %s", name, exc)
    seen: set[str] = set()
    unique = []
    for r in results:
        key = re.sub(r"\W+", " ", r["title"].lower()).strip()
        if key not in seen:
            seen.add(key)
            unique.append(r)
    return unique
