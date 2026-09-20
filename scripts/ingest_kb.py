"""Script to ingest sample knowledge base documents into Pinecone vector store."""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure project root is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.ingestion import ingest_directory
from app.services.vector_store import get_vector_store


def main() -> None:
    sample_kb_dir = Path("data/sample_kb")
    if not sample_kb_dir.exists():
        print(f"Error: Knowledge base directory '{sample_kb_dir}' not found.")
        sys.exit(1)

    print(f"=== Ingesting Knowledge Base from {sample_kb_dir} ===")
    results = ingest_directory(sample_kb_dir)

    print("\n--- Ingestion Results per File ---")
    print(f"{'Filename':<35} | {'Doc ID':<14} | {'Chunks':<8} | {'Type':<6}")
    print("-" * 72)
    total_chunks = 0
    for r in results:
        fname = r.get("source_name", "")
        doc_id = r.get("doc_id", "")
        count = r.get("chunks_count", 0)
        ftype = r.get("file_type", "")
        total_chunks += count
        print(f"{fname:<35} | {doc_id:<14} | {count:>8} | {ftype:<6}")

    print("-" * 72)
    print(f"Total files ingested: {len(results)}, Total chunks: {total_chunks}")

    print("\n--- Vector Store Statistics ---")
    store = get_vector_store()
    stats = store.stats()
    for k, v in stats.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
