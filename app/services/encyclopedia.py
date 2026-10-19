"""Encyclopedia lookup for named subjects (people, organisations, places, events), ADR-034.

Searches Wikipedia for the request's subject and returns the article as plain text. For a named subject the article
holds the dated facts (birth, records, awards, figures) that search snippets miss. No API key; results cached on
disk with the search cache. The article is ranked like any source (Wikipedia has medium authority).
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from typing import Any

import httpx

logger = logging.getLogger(__name__)

API = "https://en.wikipedia.org/w/api.php"
# Wikimedia rejects generic agents: identify the tool with a URL and contact (set RESEARCH_CONTACT in .env).
HEADERS = {"User-Agent": "DocResearchAssistant/1.0 (https://github.com/docplatform; "
                         f"{__import__('os').environ.get('RESEARCH_CONTACT', 'docplatform-research@example.org')}) httpx"}


def _cache_path(key: str) -> Any:
    from app.llm.client import get_search_cache_dir

    return get_search_cache_dir() / f"wiki_{hashlib.sha256(key.encode()).hexdigest()[:24]}.json"


_STOP = {"the", "of", "in", "on", "and", "a", "an", "for", "to", "about", "with"}
_WORK = re.compile(r"\bis an? (?:\d{4} )?(?:[\w-]+ ){0,6}(?:film|movie|album|song|single|novel|book|video game|television series|"
                   r"tv series|web series|documentary|play|musical|episode)\b", re.I)


def _words(text: str) -> set[str]:
    out = set()
    for w in re.findall(r"[\w/'-]+", text.lower()):
        w = w.strip("'-")
        if w and w not in _STOP and not re.fullmatch(r"(19|20)\d{2}", w):
            out.add(w[:-1] if len(w) > 4 and w.endswith("s") else w)
    return out


def relevant(subject: str, title: str, text: str) -> bool:
    """The article is about the subject itself: every title word is in the request, most request words are in the
    title, and it is not a film/book/song named after the subject (unless the request asks for one)."""
    sw, tw = _words(subject), _words(title)
    if not sw or not tw or not tw <= sw or len(sw & tw) / len(sw) < 0.5:
        return False
    return not (_WORK.search(text[:300]) and not _WORK.search(f"is a {subject}"))


def lookup(subject: str, *, client: httpx.Client | None = None, timeout: float = 8.0) -> dict[str, Any] | None:
    """{title, url, text} of the best-matching article, or None (no match, disambiguation page, network error)."""
    subject = (subject or "").strip()
    if len(subject) < 3:
        return None
    path = _cache_path(subject.lower())
    if path.exists():
        cached = json.loads(path.read_text(encoding="utf-8"))
        return cached or None
    own = client is None
    client = client or httpx.Client(timeout=timeout, headers=HEADERS, follow_redirects=True)
    try:
        found = client.get(API, params={"action": "query", "list": "search", "srsearch": subject, "srlimit": 3,
                                        "format": "json"}).json()
        hits = [h for h in found.get("query", {}).get("search", []) if _words(h["title"]) <= _words(subject)]
        if not hits:
            result: dict[str, Any] = {}
        else:
            title = hits[0]["title"]
            pages = client.get(API, params={"action": "query", "prop": "extracts|pageprops", "explaintext": 1,
                                            "titles": title, "redirects": 1, "format": "json"}).json()
            page = next(iter(pages.get("query", {}).get("pages", {}).values()), {})
            text = page.get("extract") or ""
            if ("disambiguation" in (page.get("pageprops") or {}) or len(text) < 400
                    or not relevant(subject, page.get("title", title), text)):
                result = {}
            else:
                result = {"title": page.get("title", title), "text": text,
                          "url": "https://en.wikipedia.org/wiki/" + page.get("title", title).replace(" ", "_")}
    except Exception as exc:
        logger.info("Encyclopedia lookup skipped for '%s': %s", subject[:60], exc)
        return None
    finally:
        if own:
            client.close()
    path.write_text(json.dumps(result), encoding="utf-8")
    return result or None
