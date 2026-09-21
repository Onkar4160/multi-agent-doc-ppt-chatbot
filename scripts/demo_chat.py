"""Demo script for Step 7: registers sample templates, triggers chat run, polls progress, and downloads artifacts."""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from pathlib import Path

# Ensure project root is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.agents.graph import graph_app
from app.agents.state import GraphState
from app.llm.client import get_llm_client
from app.services.parsers import parse_file

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DEMO_PROMPT = (
    "Research the latest Generative AI trends and create a proposal and 12-slide presentation "
    "using the same tone and style as the uploaded files."
)
SAMPLE_DOCX = Path("data/sample_templates/Company_Proposal.docx")
SAMPLE_PPTX = Path("data/sample_templates/Company_Template.pptx")


def main() -> None:
    print("=== NexaWorks Multi-Agent Chatbot Demo (Step 7: LangGraph & Chat Pipeline) ===")

    if not SAMPLE_DOCX.exists() or not SAMPLE_PPTX.exists():
        print("Error: Missing sample templates.")
        sys.exit(1)

    llm = get_llm_client()
    initial_call_count = llm.stats.call_count

    print(f"\n1. Submitting Prompt: '{DEMO_PROMPT}'")
    run_id = f"demo_run_{int(time.time())}"

    initial_state: GraphState = {
        "run_id": run_id,
        "user_message": DEMO_PROMPT,
        "file_ids": [],
        "artifacts": [],
        "errors": [],
        "retry_count": 0,
        "findings": [],
        "template_profiles": {},
    }

    print("\n2. Executing LangGraph Supervisor Pipeline...")
    start_t = time.perf_counter()
    final_state = graph_app.invoke(initial_state)
    total_time_s = time.perf_counter() - start_t

    print("\n3. Pipeline Summary & Results:")
    print("=" * 75)
    print(final_state.get("reply", "No reply generated."))
    print("=" * 75)

    validation_report = final_state.get("validation", {})
    print(f"\nValidation Report: Passed={validation_report.get('passed')}, Score={validation_report.get('score')}")
    for issue in validation_report.get("issues", []):
        print(f"  [{issue.get('severity').upper()}] {issue.get('where')}: {issue.get('message')}")

    artifacts = final_state.get("artifacts", [])
    print(f"\n4. Artifacts Generated ({len(artifacts)} files):")
    output_dir = Path("data/outputs")
    output_dir.mkdir(parents=True, exist_ok=True)

    for art in artifacts:
        print(f"  - [{art['kind'].upper()}] {art['title']}")
        print(f"    Stored File: {art.get('file_path')}")

    # Total real LLM calls
    total_llm_calls = llm.stats.call_count - initial_call_count
    print(f"\nTotal Real LLM Calls: {total_llm_calls} (Budget max 8 calls)")
    print(f"Total Pipeline Execution Time: {total_time_s:.2f} seconds")
    print(f"LLM Stats: {llm.stats.summary()}")


if __name__ == "__main__":
    main()
