"""Bulk-ingest all files in data/sample_kb/ into the vector store."""

import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv()

from app.services.kb_ingest import ingest_document


def main():
    """Ingest all documents in data/sample_kb/."""
    kb_dir = Path("data/sample_kb")
    if not kb_dir.exists():
        print(f"Directory {kb_dir} not found")
        return

    files = list(kb_dir.iterdir())
    if not files:
        print("No files found in data/sample_kb/")
        return

    project_id = 1  # default project for sample data
    total = 0
    for f in files:
        if f.is_file() and f.suffix.lower() in (".docx", ".pdf", ".pptx", ".txt"):
            print(f"Ingesting {f.name}…")
            count = ingest_document(str(f), project_id)
            print(f"  → {count} chunks")
            total += count

    print(f"\nDone. Total chunks ingested: {total}")


if __name__ == "__main__":
    main()
