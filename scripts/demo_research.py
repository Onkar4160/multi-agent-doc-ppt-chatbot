"""Script to demonstrate web research agent with search query generation and snippet synthesis."""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure project root is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.agents.web_research import research
from app.services.source_registry import SourceRegistry

DEFAULT_TOPIC = "Generative AI agentic workflow adoption in Indian enterprise 2026"


def main() -> None:
    topic = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_TOPIC

    print(f"=== Web Research Demo for Topic: '{topic}' ===")
    registry = SourceRegistry()
    res = research(topic, registry=registry)

    print("\n--- Search Queries Generated ---")
    for idx, q in enumerate(res.queries, start=1):
        print(f"  {idx}. {q}")

    print("\n--- Factual Research Findings ---")
    for idx, finding in enumerate(res.findings, start=1):
        sids = ", ".join(f"[{sid}]" for sid in finding.source_ids)
        print(f"  {idx}. {finding.text} (Sources: {sids})")

    print("\n--- Registered Sources ---")
    sources = registry.export_sources_list()
    for s in sources:
        print(f"  [{s['id']}] {s['title']}")
        print(f"      URL: {s['url']}")
        print(f"      Snippet: {s['snippet'][:120]}...")
        print()


if __name__ == "__main__":
    main()
