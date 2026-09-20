"""CLI script to run template analysis on a document or presentation file and print pretty JSON."""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Ensure root directory is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.agents.doc_analyzer import analyze_document
from app.agents.ppt_analyzer import analyze_presentation
from app.models.template_profile import TemplateProfile


def main() -> None:
    """Analyze a template file and print its TemplateProfile as pretty JSON."""
    if len(sys.argv) < 2:
        print("Usage: python scripts/demo_analyze.py <path_to_file>")
        sys.exit(1)

    file_path = Path(sys.argv[1])
    if not file_path.exists():
        print(f"Error: File not found at '{file_path}'")
        sys.exit(1)

    ext = file_path.suffix.lower()
    print(f"Analyzing '{file_path.name}' ({ext})...")

    try:
        if ext == ".pptx":
            profile: TemplateProfile = analyze_presentation(file_path)
        elif ext in (".docx", ".pdf", ".png", ".jpg", ".jpeg"):
            profile: TemplateProfile = analyze_document(file_path)
        else:
            print(f"Error: Unsupported file extension '{ext}'")
            sys.exit(1)

        print("\n=== TemplateProfile Result (Pretty JSON) ===")
        print(profile.model_dump_json(indent=2))

    except Exception as exc:
        print(f"Analysis failed: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
